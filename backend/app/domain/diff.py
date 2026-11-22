from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .models import Flow


class Change(BaseModel):
    kind: Literal["added", "removed", "changed"]
    target: Literal["node", "edge", "trigger", "start", "name"]
    id: str | None = None
    before: Any = None
    after: Any = None


def diff_flows(before: Flow | None, after: Flow | None) -> list[Change]:
    if after is None:
        return []
    b_nodes = {n.id: n.model_dump(mode="json") for n in before.nodes} if before else {}
    a_nodes = {n.id: n.model_dump(mode="json") for n in after.nodes}
    b_edges = {e.key: e.to for e in before.edges} if before else {}
    a_edges = {e.key: e.to for e in after.edges}

    out: list[Change] = []
    if before is None:
        out.append(Change(kind="added", target="trigger", after=after.trigger.model_dump()))
        if after.start_node_id:
            out.append(Change(kind="added", target="start", after=after.start_node_id))
    else:
        if before.name != after.name:
            out.append(Change(kind="changed", target="name", before=before.name, after=after.name))
        if before.trigger != after.trigger:
            out.append(Change(kind="changed", target="trigger",
                              before=before.trigger.model_dump(), after=after.trigger.model_dump()))
        if before.start_node_id != after.start_node_id:
            out.append(Change(kind="changed", target="start",
                              before=before.start_node_id, after=after.start_node_id))

    for nid, node in a_nodes.items():
        if nid not in b_nodes:
            out.append(Change(kind="added", target="node", id=nid, after=node))
        elif b_nodes[nid] != node:
            out.append(Change(kind="changed", target="node", id=nid,
                              before=b_nodes[nid], after=node))
    for nid, node in b_nodes.items():
        if nid not in a_nodes:
            out.append(Change(kind="removed", target="node", id=nid, before=node))

    for key, to in a_edges.items():
        if key not in b_edges:
            out.append(Change(kind="added", target="edge", id=key, after=to))
        elif b_edges[key] != to:
            out.append(Change(kind="changed", target="edge", id=key, before=b_edges[key], after=to))
    for key, to in b_edges.items():
        if key not in a_edges:
            out.append(Change(kind="removed", target="edge", id=key, before=to))
    return out
