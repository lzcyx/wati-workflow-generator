from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .models import Edge, Flow, NodeAdapter, Trigger
from .registry import spec_for


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AddNode(_Op):
    op: Literal["add_node"]
    node: dict[str, Any]


class UpdateNode(_Op):
    op: Literal["update_node"]
    id: str
    changes: dict[str, Any]


class RemoveNode(_Op):
    op: Literal["remove_node"]
    id: str


class Connect(_Op):
    op: Literal["connect"]
    from_: str = Field(alias="from")
    handle: str
    to: str


class Disconnect(_Op):
    op: Literal["disconnect"]
    from_: str = Field(alias="from")
    handle: str


class SetTrigger(_Op):
    op: Literal["set_trigger"]
    trigger: Trigger


class SetStart(_Op):
    op: Literal["set_start"]
    node_id: str


EditOp = Annotated[
    AddNode | UpdateNode | RemoveNode | Connect | Disconnect | SetTrigger | SetStart,
    Field(discriminator="op"),
]
EditOpAdapter: TypeAdapter[EditOp] = TypeAdapter(EditOp)


class EditError(BaseModel):
    index: int
    message: str


def format_validation_error(e: ValidationError, limit: int = 6) -> str:
    parts = []
    for d in e.errors()[:limit]:
        loc = ".".join(str(x) for x in d["loc"]) or "(root)"
        parts.append(f"{loc}: {d['msg']}")
    more = len(e.errors()) - limit
    if more > 0:
        parts.append(f"... and {more} more")
    return "; ".join(parts)


class _OpFailed(Exception):
    pass


def apply_edits(flow: Flow, raw_ops: list[Any]) -> tuple[Flow, list[EditError]]:
    draft = flow.model_copy(deep=True)
    errors: list[EditError] = []
    for i, raw in enumerate(raw_ops):
        try:
            op = EditOpAdapter.validate_python(raw)
        except ValidationError as e:
            errors.append(EditError(index=i, message=format_validation_error(e)))
            continue
        try:
            _apply_one(draft, op)
        except _OpFailed as e:
            errors.append(EditError(index=i, message=str(e)))
    if errors:
        return flow, errors
    return draft, []


def _apply_one(f: Flow, op: EditOp) -> None:
    nodes = f.node_map()
    match op:
        case AddNode(node=raw):
            try:
                node = NodeAdapter.validate_python(raw)
            except ValidationError as e:
                raise _OpFailed(f"invalid node: {format_validation_error(e)}") from e
            if node.id in nodes:
                raise _OpFailed(f"node id '{node.id}' already exists")
            f.nodes.append(node)
        case UpdateNode(id=nid, changes=changes):
            old = nodes.get(nid)
            if old is None:
                raise _OpFailed(f"node '{nid}' does not exist")
            if "type" in changes and changes["type"] != old.type:
                raise _OpFailed("cannot change a node's type; remove and add a new node")
            if "id" in changes and changes["id"] != nid:
                raise _OpFailed("cannot rename a node id")
            merged = {**old.model_dump(mode="json"), **changes}
            try:
                new = NodeAdapter.validate_python(merged)
            except ValidationError as e:
                raise _OpFailed(f"invalid changes: {format_validation_error(e)}") from e
            f.nodes = [new if n.id == nid else n for n in f.nodes]
        case RemoveNode(id=nid):
            if nid not in nodes:
                raise _OpFailed(f"node '{nid}' does not exist")
            f.nodes = [n for n in f.nodes if n.id != nid]
            f.edges = [e for e in f.edges if e.from_ != nid and e.to != nid]
            if f.start_node_id == nid:
                f.start_node_id = None
        case Connect(from_=src, handle=h, to=dst):
            if src not in nodes:
                raise _OpFailed(f"source node '{src}' does not exist")
            if dst not in nodes:
                raise _OpFailed(f"target node '{dst}' does not exist")
            valid = spec_for(nodes[src]).handles(nodes[src])
            if h not in valid:
                raise _OpFailed(f"'{h}' is not a valid exit of '{src}'. Valid: {', '.join(valid)}")
            f.edges = [e for e in f.edges if not (e.from_ == src and e.handle == h)]
            f.edges.append(Edge(from_=src, handle=h, to=dst))
        case Disconnect(from_=src, handle=h):
            before = len(f.edges)
            f.edges = [e for e in f.edges if not (e.from_ == src and e.handle == h)]
            if len(f.edges) == before:
                raise _OpFailed(f"no edge on exit {src}:{h}")
        case SetTrigger(trigger=t):
            f.trigger = t
        case SetStart(node_id=nid):
            if nid not in nodes:
                raise _OpFailed(f"node '{nid}' does not exist")
            f.start_node_id = nid
