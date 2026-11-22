from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from threading import Event
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..copilot.agent import Agent, TurnResult
from ..domain.engine import ApiMock, Engine, SimState
from ..domain.models import Flow
from ..domain.validator import has_errors, validate_flow
from ..llm.client import LLMError
from ..store import Session, Store

router = APIRouter(prefix="/api")


def _store(req: Request) -> Store:
    return req.app.state.store


def _agent(req: Request) -> Agent:
    try:
        return req.app.state.agent_factory()
    except LLMError as e:
        raise HTTPException(503, f"LLM is not available: {e}") from e


class SessionOut(BaseModel):
    id: str
    messages: list[dict[str, Any]]
    flow: dict[str, Any] | None
    version: int | None
    versions: list[int]
    issues: list[dict[str, Any]]
    workspace: dict[str, Any]


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class SimulationIn(BaseModel):
    api_results: dict[str, Literal["success", "failure"]] = Field(default_factory=dict)
    version: int | None = None


class SimInputIn(BaseModel):
    text: str = Field(max_length=1000)


class SimulationOut(BaseModel):
    id: str
    flow_version: int
    state: SimState


def _session_out(req: Request, sid: str) -> SessionOut:
    s = _store(req).get_session(sid)
    if s is None:
        raise HTTPException(404, "session not found")
    ws = req.app.state.workspace
    return SessionOut(
        id=s.id, messages=s.messages,
        flow=s.current.dump() if s.current else None, version=s.current_version,
        versions=[f.version for f in s.versions],
        issues=[i.model_dump() for i in validate_flow(s.current, ws)] if s.current else [],
        workspace=ws.model_dump())


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/sessions", response_model=SessionOut)
def create_session(req: Request) -> SessionOut:
    s = _store(req).create_session()
    return _session_out(req, s.id)


@router.get("/sessions/{sid}", response_model=SessionOut)
def get_session(sid: str, req: Request) -> SessionOut:
    return _session_out(req, sid)


@router.get("/sessions/{sid}/versions/{version}")
def get_version(sid: str, version: int, req: Request) -> dict[str, Any]:
    s = _store(req).get_session(sid)
    if s is None:
        raise HTTPException(404, "session not found")
    for f in s.versions:
        if f.version == version:
            return f.dump()
    raise HTTPException(404, "version not found")


@router.post(
    "/sessions/{sid}/messages",
    response_model=TurnResult,
    responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}},
)
def post_message(sid: str, body: MessageIn, req: Request) -> TurnResult | StreamingResponse:
    s = _store(req).get_session(sid)
    if s is None:
        raise HTTPException(404, "session not found")
    agent = _agent(req)
    if req.headers.get("accept") == "application/x-ndjson":
        return StreamingResponse(
            _turn_events(agent, s, body.content),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return agent.run_turn(s, body.content)


async def _turn_events(agent: Agent, session: Session, content: str) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    disconnected = Event()

    def publish(event: dict[str, Any]) -> None:
        if not disconnected.is_set():
            loop.call_soon_threadsafe(events.put_nowait, event)

    def run() -> None:
        try:
            turn = agent.run_turn(session, content, on_update=publish)
            publish({"type": "result", "turn": turn.model_dump(mode="json")})
        except Exception:
            logging.getLogger(__name__).exception("Could not stream turn for %s", session.id)
            publish({"type": "error", "message": "The server could not finish sending the reply."})

    worker = loop.run_in_executor(None, run)
    try:
        while True:
            try:
                event = await asyncio.wait_for(events.get(), timeout=15)
            except TimeoutError:
                yield "\n"
                continue
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] in ("result", "error"):
                return
    finally:
        disconnected.set()
        await asyncio.shield(worker)


@router.post("/sessions/{sid}/simulations", response_model=SimulationOut)
def start_simulation(sid: str, body: SimulationIn, req: Request) -> SimulationOut:
    store = _store(req)
    s = store.get_session(sid)
    if s is None:
        raise HTTPException(404, "session not found")
    flow: Flow | None = s.current
    if body.version is not None:
        flow = next((f for f in s.versions if f.version == body.version), None)
    if flow is None:
        raise HTTPException(409, "no flow to simulate yet")
    issues = validate_flow(flow, req.app.state.workspace)
    if has_errors(issues):
        raise HTTPException(422, {"message": "flow has validation errors",
                                  "issues": [i.model_dump() for i in issues]})
    engine = Engine(flow, api_mocks={k: ApiMock(outcome=v) for k, v in body.api_results.items()},
                    max_steps=req.app.state.settings.sim_max_steps)
    engine.start()
    sim = store.add_simulation(s, engine)
    return SimulationOut(id=sim.id, flow_version=sim.flow_version, state=engine.state)


@router.post("/simulations/{sim_id}/input", response_model=SimulationOut)
def simulation_input(sim_id: str, body: SimInputIn, req: Request) -> SimulationOut:
    sim = _store(req).get_simulation(sim_id)
    if sim is None:
        raise HTTPException(404, "simulation not found")
    try:
        sim.engine.send_input(body.text)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return SimulationOut(id=sim.id, flow_version=sim.flow_version, state=sim.engine.state)
