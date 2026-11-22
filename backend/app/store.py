from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from .domain.engine import Engine
from .domain.models import Flow


@dataclass(frozen=True)
class ConversationSummary:
    text: str
    message_count: int


@dataclass
class Session:
    id: str
    flow_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    versions: list[Flow] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    context_summary: ConversationSummary | None = None

    @property
    def current(self) -> Flow | None:
        return self.versions[-1] if self.versions else None

    @property
    def current_version(self) -> int | None:
        return self.current.version if self.current else None


@dataclass
class Simulation:
    id: str
    session_id: str
    flow_version: int
    engine: Engine


class Store:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.simulations: dict[str, Simulation] = {}

    def create_session(self) -> Session:
        sid = f"s_{uuid.uuid4().hex[:10]}"
        s = Session(id=sid, flow_id=f"flow_{uuid.uuid4().hex[:8]}")
        self.sessions[sid] = s
        return s

    def get_session(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def add_simulation(self, session: Session, engine: Engine) -> Simulation:
        sim = Simulation(id=f"sim_{uuid.uuid4().hex[:10]}", session_id=session.id,
                         flow_version=engine.flow.version, engine=engine)
        self.simulations[sim.id] = sim
        return sim

    def get_simulation(self, sim_id: str) -> Simulation | None:
        return self.simulations.get(sim_id)
