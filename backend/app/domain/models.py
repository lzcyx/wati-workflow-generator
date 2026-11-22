from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NewContactMessageTrigger(_Base):
    type: Literal["new_contact_message"] = "new_contact_message"


class KeywordTrigger(_Base):
    type: Literal["keyword"] = "keyword"
    keywords: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


Trigger = Annotated[NewContactMessageTrigger | KeywordTrigger, Field(discriminator="type")]


class _NodeBase(_Base):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class SendMessageNode(_NodeBase):
    type: Literal["send_message"] = "send_message"
    text: str = Field(min_length=1)


class AskQuestionNode(_NodeBase):
    type: Literal["ask_question"] = "ask_question"
    text: str = Field(min_length=1)
    mode: Literal["buttons", "text"] = "buttons"
    options: list[str] = Field(default_factory=list)
    save_as: str | None = None


class ConditionNode(_NodeBase):
    type: Literal["condition"] = "condition"
    variable: str = Field(min_length=1)
    operator: Literal["equals", "not_equals", "contains"] = "equals"
    value: str


class ApiCallNode(_NodeBase):
    type: Literal["api_call"] = "api_call"
    method: Literal["GET", "POST"] = "GET"
    url: str = Field(min_length=1)
    body: dict[str, Any] | str | None = None
    save_as: str | None = None


class AssignTeamNode(_NodeBase):
    type: Literal["assign_team"] = "assign_team"
    team: str = Field(min_length=1)


class WaitNode(_NodeBase):
    type: Literal["wait"] = "wait"
    duration_seconds: int


Node = Annotated[
    SendMessageNode | AskQuestionNode | ConditionNode | ApiCallNode | AssignTeamNode | WaitNode,
    Field(discriminator="type"),
]
NodeAdapter: TypeAdapter[Node] = TypeAdapter(Node)
TriggerAdapter: TypeAdapter[Trigger] = TypeAdapter(Trigger)


class Edge(_Base):
    from_: str = Field(alias="from")
    handle: str
    to: str

    @property
    def key(self) -> str:
        return f"{self.from_}:{self.handle}"


class Flow(_Base):
    id: str = "flow"
    version: int = 0
    name: str = "Untitled flow"
    trigger: Trigger = Field(default_factory=NewContactMessageTrigger)
    start_node_id: str | None = None
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def node_map(self) -> dict[str, Node]:
        out: dict[str, Node] = {}
        for n in self.nodes:
            out.setdefault(n.id, n)
        return out

    def get_node(self, node_id: str) -> Node | None:
        return self.node_map().get(node_id)

    def edge_from(self, node_id: str, handle: str) -> Edge | None:
        for e in self.edges:
            if e.from_ == node_id and e.handle == handle:
                return e
        return None

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class Workspace(_Base):
    teams: list[str] = Field(default_factory=lambda: ["Sales", "Support", "Billing"])


DEFAULT_WORKSPACE = Workspace()
