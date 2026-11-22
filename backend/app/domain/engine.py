from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import (
    ApiCallNode,
    AskQuestionNode,
    AssignTeamNode,
    ConditionNode,
    Flow,
    SendMessageNode,
    WaitNode,
)
from .text import TEMPLATE_RE, match_option, normalize

Status = Literal["running", "waiting_input", "completed", "assigned", "error", "max_steps"]


class ApiMock(BaseModel):
    outcome: Literal["success", "failure"] = "success"
    body: Any = Field(default_factory=lambda: {"ok": True})


class TranscriptEntry(BaseModel):
    role: Literal["bot", "user", "system"]
    text: str
    node_id: str | None = None
    options: list[str] | None = None


class TraceEntry(BaseModel):
    node_id: str
    type: str
    detail: str
    handle: str | None = None


class SimState(BaseModel):
    current_node_id: str | None
    variables: dict[str, Any]
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)
    status: Status = "running"
    error: str | None = None
    assigned_team: str | None = None
    steps: int = 0


class _MissingVar(Exception):
    pass


def _lookup(variables: dict[str, Any], name: str) -> Any:
    if name in variables:
        return variables[name]
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        root = ".".join(parts[:i])
        if root in variables:
            cur = variables[root]
            for p in parts[i:]:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    raise _MissingVar(name)
            return cur
    raise _MissingVar(name)


def _render(text: str, variables: dict[str, Any]) -> str:
    def sub(m: Any) -> str:
        v = _lookup(variables, m.group(1))
        return v if isinstance(v, str) else str(v)

    return TEMPLATE_RE.sub(sub, text)


class Engine:
    def __init__(
        self,
        flow: Flow,
        api_mocks: dict[str, ApiMock] | None = None,
        max_steps: int = 100,
        contact: dict[str, str] | None = None,
    ):
        self.flow = flow
        self.nodes = flow.node_map()
        self.api_mocks = api_mocks or {}
        self.max_steps = max_steps
        contact = contact or {"name": "Alex", "phone": "+15550001111"}
        self.state = SimState(
            current_node_id=flow.start_node_id,
            variables={f"contact.{k}": v for k, v in contact.items()},
        )


    def start(self) -> SimState:
        self._run()
        return self.state

    def send_input(self, text: str) -> SimState:
        st = self.state
        if st.status != "waiting_input":
            raise ValueError(f"simulation is not waiting for input (status={st.status})")
        node = self.nodes[st.current_node_id]
        assert isinstance(node, AskQuestionNode)
        st.transcript.append(TranscriptEntry(role="user", text=text))
        if node.mode == "text":
            handle = "next"
            value: str | None = text.strip()
        else:
            matched = match_option(node.options, text)
            handle = f"option:{matched}" if matched is not None else "fallback"
            value = matched
        if value is not None and node.save_as:
            st.variables[node.save_as] = value
        st.trace.append(TraceEntry(node_id=node.id, type=node.type, handle=handle,
                                   detail=f"reply {text!r} -> {handle}"))
        st.status = "running"
        self._follow(node.id, handle)
        self._run()
        return st


    def _follow(self, node_id: str, handle: str) -> None:
        edge = self.flow.edge_from(node_id, handle)
        self.state.current_node_id = edge.to if edge else None
        if edge is None:
            node = self.nodes[node_id]
            if isinstance(node, AssignTeamNode):
                self.state.status = "assigned"
            elif isinstance(node, SendMessageNode):
                self.state.status = "completed"
            else:
                self._fail(f"exit '{handle}' of '{node_id}' is not connected")

    def _fail(self, msg: str) -> None:
        self.state.status = "error"
        self.state.error = msg
        self.state.transcript.append(TranscriptEntry(role="system", text=f"Error: {msg}"))

    def _run(self) -> None:
        st = self.state
        while st.status == "running":
            nid = st.current_node_id
            if nid is None or nid not in self.nodes:
                self._fail(f"node '{nid}' does not exist")
                return
            if st.steps >= self.max_steps:
                st.status = "max_steps"
                st.transcript.append(TranscriptEntry(
                    role="system", text=f"Stopped after {self.max_steps} steps."))
                return
            st.steps += 1
            node = self.nodes[nid]
            try:
                self._step(node)
            except _MissingVar as e:
                self._fail(f"variable '{e.args[0]}' is not set at '{nid}'")
                return

    def _step(self, node: Any) -> None:
        st = self.state
        if isinstance(node, SendMessageNode):
            text = _render(node.text, st.variables)
            st.transcript.append(TranscriptEntry(role="bot", text=text, node_id=node.id))
            st.trace.append(TraceEntry(node_id=node.id, type=node.type, handle="next",
                                       detail="sent message"))
            self._follow(node.id, "next")
        elif isinstance(node, AskQuestionNode):
            text = _render(node.text, st.variables)
            st.transcript.append(TranscriptEntry(
                role="bot", text=text, node_id=node.id,
                options=list(node.options) if node.mode == "buttons" else None))
            st.trace.append(TraceEntry(node_id=node.id, type=node.type, detail="asked question"))
            st.status = "waiting_input"
        elif isinstance(node, ConditionNode):
            actual = _lookup(st.variables, node.variable)
            a, v = normalize(str(actual)), normalize(node.value)
            result = {
                "equals": a == v,
                "not_equals": a != v,
                "contains": v in a,
            }[node.operator]
            handle = "true" if result else "false"
            st.trace.append(TraceEntry(
                node_id=node.id, type=node.type, handle=handle,
                detail=f"{node.variable}={actual!r} {node.operator} {node.value!r} -> {handle}"))
            self._follow(node.id, handle)
        elif isinstance(node, ApiCallNode):
            url = _render(node.url, st.variables)
            mock = self.api_mocks.get(node.id, ApiMock())
            handle = mock.outcome
            if handle == "success" and node.save_as:
                st.variables[node.save_as] = mock.body
            st.trace.append(TraceEntry(node_id=node.id, type=node.type, handle=handle,
                                       detail=f"{node.method} {url} -> {handle} (mocked)"))
            self._follow(node.id, handle)
        elif isinstance(node, AssignTeamNode):
            st.assigned_team = node.team
            st.transcript.append(TranscriptEntry(
                role="system", text=f"Conversation assigned to team {node.team}.",
                node_id=node.id))
            st.trace.append(TraceEntry(node_id=node.id, type=node.type, handle="next",
                                       detail=f"assigned to {node.team}"))
            self._follow(node.id, "next")
        elif isinstance(node, WaitNode):
            st.transcript.append(TranscriptEntry(
                role="system", text=f"(waited {node.duration_seconds}s)", node_id=node.id))
            st.trace.append(TraceEntry(node_id=node.id, type=node.type, handle="next",
                                       detail=f"waited {node.duration_seconds}s (simulated)"))
            self._follow(node.id, "next")
        else:
            self._fail(f"unknown node type {node.type}")
