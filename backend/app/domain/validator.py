from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from .models import (
    DEFAULT_WORKSPACE,
    ApiCallNode,
    AskQuestionNode,
    AssignTeamNode,
    Flow,
    Node,
    WaitNode,
    Workspace,
)
from .registry import (
    CONTACT_VARS,
    MAX_OPTIONS,
    WAIT_MAX_SECONDS,
    WAIT_MIN_SECONDS,
    spec_for,
)
from .text import normalize, var_root_candidates

SENSITIVE_RE = re.compile(
    r"password|passcode|card\s*number|credit\s*card|\bcvv\b|\bcvc\b|\botp\b|\bpin\b|"
    r"social\s*security|\bssn\b",
    re.IGNORECASE,
)


class Issue(BaseModel):
    level: Literal["error", "warning"]
    code: str
    message: str
    node_id: str | None = None


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


def validate_flow(flow: Flow, workspace: Workspace = DEFAULT_WORKSPACE) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, msg: str, node_id: str | None = None) -> None:
        issues.append(Issue(level="error", code=code, message=msg, node_id=node_id))

    def warn(code: str, msg: str, node_id: str | None = None) -> None:
        issues.append(Issue(level="warning", code=code, message=msg, node_id=node_id))

    seen: set[str] = set()
    for n in flow.nodes:
        if n.id in seen:
            err("duplicate_node_id", f"Node id '{n.id}' is used more than once.", n.id)
        seen.add(n.id)
    nodes = flow.node_map()

    if not flow.nodes:
        err("empty_flow", "The flow has no nodes.")
    if flow.start_node_id is None:
        if flow.nodes:
            err("missing_start", "No start node is set.")
    elif flow.start_node_id not in nodes:
        err("missing_start", f"Start node '{flow.start_node_id}' does not exist.")

    for n in flow.nodes:
        if isinstance(n, AskQuestionNode):
            if n.mode == "buttons":
                if not (1 <= len(n.options) <= MAX_OPTIONS):
                    err("option_count",
                        f"Question '{n.id}' must have 1-{MAX_OPTIONS} options "
                        f"(has {len(n.options)}).", n.id)
                norm_seen: dict[str, str] = {}
                for o in n.options:
                    k = normalize(o)
                    if not k:
                        err("empty_option", f"Question '{n.id}' has an empty option.", n.id)
                    elif k in norm_seen:
                        err("duplicate_option",
                            f"Question '{n.id}' has duplicate options "
                            f"'{norm_seen[k]}' and '{o}'.", n.id)
                    else:
                        norm_seen[k] = o
            if SENSITIVE_RE.search(n.text):
                warn("sensitive_data",
                     f"Question '{n.id}' looks like it collects sensitive data "
                     "(password / card / OTP...). Avoid this in chat.", n.id)
        elif isinstance(n, WaitNode):
            if not (WAIT_MIN_SECONDS <= n.duration_seconds <= WAIT_MAX_SECONDS):
                err("wait_range",
                    f"Wait '{n.id}' must be {WAIT_MIN_SECONDS}-{WAIT_MAX_SECONDS} seconds "
                    f"(got {n.duration_seconds}).", n.id)
        elif isinstance(n, AssignTeamNode):
            if n.team not in workspace.teams:
                err("unknown_team",
                    f"Team '{n.team}' does not exist. Available: {', '.join(workspace.teams)}.",
                    n.id)
        elif isinstance(n, ApiCallNode):
            if not n.url.lower().startswith("https://"):
                warn("insecure_url", f"API call '{n.id}' does not use https.", n.id)

    edge_keys: set[str] = set()
    for e in flow.edges:
        src = nodes.get(e.from_)
        if src is None:
            err("edge_bad_source", f"Edge from unknown node '{e.from_}'.")
            continue
        if e.to not in nodes:
            err("edge_bad_target", f"Edge {e.key} points to unknown node '{e.to}'.", e.from_)
        if e.handle not in spec_for(src).handles(src):
            err("bad_handle",
                f"'{e.handle}' is not a valid exit of {src.type} '{src.id}'. "
                f"Valid: {', '.join(spec_for(src).handles(src))}.", src.id)
        if e.key in edge_keys:
            err("duplicate_edge", f"Exit {e.key} has more than one edge.", src.id)
        edge_keys.add(e.key)

    for n in flow.nodes:
        spec = spec_for(n)
        if spec.can_terminate:
            continue
        for h in spec.handles(n):
            if f"{n.id}:{h}" not in edge_keys:
                err("unconnected_exit",
                    f"Exit '{h}' of {n.type} '{n.id}' is not connected.", n.id)

    for comp in _cycles(flow, nodes):
        if not any(isinstance(nodes[i], AskQuestionNode) for i in comp):
            ids = ", ".join(sorted(comp))
            err("infinite_loop",
                f"Loop through [{ids}] has no Ask Question, so it would run forever.",
                sorted(comp)[0])

    reachable = _reachable(flow, nodes)
    for n in flow.nodes:
        if flow.start_node_id in nodes and n.id not in reachable:
            warn("unreachable", f"Node '{n.id}' can't be reached from the start node.", n.id)

    if flow.start_node_id in nodes:
        defined_in = _definite_assignment(flow, nodes, reachable)
        for nid in [n.id for n in nodes.values() if n.id in reachable]:
            node = nodes[nid]
            avail = defined_in.get(nid, set())
            for var in sorted(spec_for(node).reads(node)):
                if not any(c in avail for c in var_root_candidates(var)):
                    err("undefined_variable",
                        f"'{nid}' uses '{var}', but at least one path reaching it never "
                        "sets that variable.", nid)

    return issues


def _out_edges(flow: Flow, nodes: dict[str, Node]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {nid: [] for nid in nodes}
    for e in flow.edges:
        if e.from_ in nodes and e.to in nodes:
            src = nodes[e.from_]
            if e.handle in spec_for(src).handles(src):
                out[e.from_].append((e.handle, e.to))
    return out


def _reachable(flow: Flow, nodes: dict[str, Node]) -> set[str]:
    if flow.start_node_id not in nodes:
        return set()
    out = _out_edges(flow, nodes)
    seen = {flow.start_node_id}
    stack = [flow.start_node_id]
    while stack:
        cur = stack.pop()
        for _h, to in out[cur]:
            if to not in seen:
                seen.add(to)
                stack.append(to)
    return seen


def _definite_assignment(
    flow: Flow, nodes: dict[str, Node], reachable: set[str]
) -> dict[str, set[str]]:
    out = _out_edges(flow, nodes)
    all_vars = set(CONTACT_VARS)
    for nid in reachable:
        n = nodes[nid]
        for h in spec_for(n).handles(n):
            all_vars |= spec_for(n).writes(n, h)

    start = flow.start_node_id
    assert start is not None
    state: dict[str, set[str]] = {nid: set(all_vars) for nid in reachable}
    state[start] = set(CONTACT_VARS)

    incoming: dict[str, list[tuple[str, str]]] = {nid: [] for nid in reachable}
    for src in reachable:
        for h, to in out[src]:
            incoming[to].append((src, h))

    changed = True
    while changed:
        changed = False
        for nid in reachable:
            contributions = [
                state[src] | spec_for(nodes[src]).writes(nodes[src], h)
                for src, h in incoming[nid]
            ]
            if nid == start:
                contributions.append(set(CONTACT_VARS))
            new = set.intersection(*contributions) if contributions else set(CONTACT_VARS)
            if new != state[nid]:
                state[nid] = new
                changed = True
    return state


def _cycles(flow: Flow, nodes: dict[str, Node]) -> list[set[str]]:
    out = _out_edges(flow, nodes)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[set[str]] = []
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for _h, w in out[v]:
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp: set[str] = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.add(w)
                if w == v:
                    break
            if len(comp) > 1 or any(to == v for _h, to in out[v]):
                result.append(comp)

    for v in nodes:
        if v not in index:
            strong(v)
    return result
