from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..domain.diff import Change, diff_flows
from ..domain.edits import apply_edits, format_validation_error
from ..domain.engine import ApiMock, Engine
from ..domain.models import Flow, Workspace
from ..domain.validator import Issue, has_errors, validate_flow

MAX_SUGGESTIONS = 3
SUGGESTIONS_SCHEMA = {
    "type": "array", "items": {"type": "string"}, "maxItems": MAX_SUGGESTIONS,
    "description": "Up to 3 short English replies the user could send next, written in their "
                   "voice (e.g. likely answers to your questions or useful next edits). "
                   "Shown as one-click buttons.",
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "replace_flow",
            "description": "Write the whole flow (first version, or user asked to start over). "
                           "Returns validation issues.",
            "parameters": {
                "type": "object",
                "properties": {"flow": {"type": "object", "description": "Full flow JSON"}},
                "required": ["flow"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_edits",
            "description": "Apply an atomic batch of edit operations to the existing flow. "
                           "Returns validation issues and the diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ops": {"type": "array", "items": {"type": "object"},
                            "description": "Edit operations"}
                },
                "required": ["ops"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate",
            "description": "Run the current draft against scenarios. Each scenario is a list of "
                           "user replies; optionally force API call outcomes by node id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenarios": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "inputs": {"type": "array", "items": {"type": "string"}},
                                "api_results": {
                                    "type": "object",
                                    "description": 'node_id -> "success" | "failure"',
                                },
                            },
                        },
                    }
                },
                "required": ["scenarios"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user 1-3 clarifying questions. Ends the turn; the draft is "
                           "discarded.",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "suggestions": SUGGESTIONS_SCHEMA,
                },
                "required": ["questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Submit the draft and end the turn. Rejected if validation errors "
                           "remain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Reply shown to the user"},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "unsupported": {"type": "array", "items": {"type": "string"},
                                    "description": "Requirements that could not be built"},
                    "suggestions": SUGGESTIONS_SCHEMA,
                },
                "required": ["summary"],
            },
        },
    },
]

TERMINAL_TOOLS = {"ask_user", "finish"}


class Scenario(BaseModel):
    name: str | None = None
    inputs: list[str] = Field(default_factory=list)
    api_results: dict[str, Literal["success", "failure"]] = Field(default_factory=dict)


class SimulateArgs(BaseModel):
    scenarios: list[Scenario] = Field(min_length=1, max_length=10)


def _unescape(s: str) -> str:
    return s.replace("\\n", "\n") if "\n" not in s else s


def clean_suggestions(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str):
            t = _unescape(item).strip()
            if t and len(t) <= 300 and t not in out:
                out.append(t)
    return out[:MAX_SUGGESTIONS]


class _EndArgs(BaseModel):
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("suggestions", mode="before")
    @classmethod
    def _sugg(cls, v: Any) -> list[str]:
        return clean_suggestions(v)


class AskUserArgs(_EndArgs):
    questions: list[str] = Field(min_length=1, max_length=3)


class FinishArgs(_EndArgs):
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _clean(cls, v: str) -> str:
        return _unescape(v).strip()


@dataclass
class ToolContext:
    base: Flow | None
    workspace: Workspace
    sim_max_steps: int
    draft: Flow | None = None
    issues: list[Issue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.draft is None and self.base is not None:
            self.draft = self.base.model_copy(deep=True)
            self.issues = validate_flow(self.draft, self.workspace)


@dataclass
class ToolOutcome:
    result: dict[str, Any]
    ok: bool
    terminal: bool = False
    finish: FinishArgs | None = None
    questions: list[str] | None = None
    suggestions: list[str] = field(default_factory=list)


def _outline(flow: Flow) -> dict[str, Any]:
    return {
        "start_node_id": flow.start_node_id,
        "nodes": [f"{n.id} ({n.type})" for n in flow.nodes],
        "edges": [f"{e.from_} --{e.handle}--> {e.to}" for e in flow.edges],
    }


def _issues_payload(issues: list[Issue]) -> list[dict[str, Any]]:
    return [i.model_dump(exclude_none=True) for i in issues]


def _diff_payload(changes: list[Change]) -> list[str]:
    return [f"{c.kind} {c.target} {c.id or ''}".strip() for c in changes]


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolOutcome:
    handler = _HANDLERS.get(name)
    if handler is None:
        return ToolOutcome({"error": f"unknown tool '{name}'"}, ok=False)
    try:
        return handler(ctx, args)
    except ValidationError as e:
        return ToolOutcome({"error": f"invalid arguments: {format_validation_error(e)}"}, ok=False)


def _replace_flow(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = args.get("flow")
    if not isinstance(raw, dict):
        return ToolOutcome({"error": "`flow` must be an object"}, ok=False)
    raw = {k: v for k, v in raw.items() if k not in ("id", "version")}
    try:
        flow = Flow.model_validate(raw)
    except ValidationError as e:
        return ToolOutcome(
            {"error": f"flow does not match the schema (draft unchanged): "
                      f"{format_validation_error(e, limit=10)}"}, ok=False)
    if ctx.base is not None:
        flow.id, flow.version = ctx.base.id, ctx.base.version
    ctx.draft = flow
    ctx.issues = validate_flow(flow, ctx.workspace)
    return ToolOutcome({
        "ok": True,
        "outline": _outline(flow),
        "issues": _issues_payload(ctx.issues),
        "diff_vs_current": _diff_payload(diff_flows(ctx.base, flow)),
    }, ok=True)


def _apply_edits(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    if ctx.draft is None:
        return ToolOutcome({"error": "no flow exists yet — use replace_flow first"}, ok=False)
    ops = args.get("ops")
    if not isinstance(ops, list) or not ops:
        return ToolOutcome({"error": "`ops` must be a non-empty array"}, ok=False)
    before = ctx.draft
    new, errors = apply_edits(before, ops)
    if errors:
        return ToolOutcome({
            "error": "batch rejected, nothing applied",
            "op_errors": [e.model_dump() for e in errors],
        }, ok=False)
    ctx.draft = new
    ctx.issues = validate_flow(new, ctx.workspace)
    return ToolOutcome({
        "ok": True,
        "outline": _outline(new),
        "issues": _issues_payload(ctx.issues),
        "diff": _diff_payload(diff_flows(before, new)),
    }, ok=True)


def _simulate(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    parsed = SimulateArgs.model_validate(args)
    if ctx.draft is None:
        return ToolOutcome({"error": "no flow to simulate"}, ok=False)
    if has_errors(ctx.issues):
        return ToolOutcome({"error": "fix validation errors before simulating",
                            "issues": _issues_payload(ctx.issues)}, ok=False)
    results = []
    for sc in parsed.scenarios:
        mocks = {nid: ApiMock(outcome=o) for nid, o in sc.api_results.items()}
        eng = Engine(ctx.draft, api_mocks=mocks, max_steps=ctx.sim_max_steps)
        st = eng.start()
        unused = []
        for text in sc.inputs:
            if st.status != "waiting_input":
                unused.append(text)
                continue
            st = eng.send_input(text)
        results.append({
            "name": sc.name,
            "status": st.status,
            "path": [f"{t.node_id}{'['+t.handle+']' if t.handle else ''}" for t in st.trace],
            "bot_messages": [t.text for t in st.transcript if t.role != "user"],
            "assigned_team": st.assigned_team,
            "unused_inputs": unused,
            "error": st.error,
        })
    return ToolOutcome({"ok": True, "results": results}, ok=True)


def _ask_user(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    parsed = AskUserArgs.model_validate(args)
    return ToolOutcome({"ok": True}, ok=True, terminal=True, questions=parsed.questions,
                       suggestions=parsed.suggestions)


def _finish(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    parsed = FinishArgs.model_validate(args)
    if ctx.draft is not None and has_errors(ctx.issues):
        return ToolOutcome({
            "error": "finish rejected: the draft still has validation errors. Fix them, or "
                     "undo the change and list the requirement in `unsupported`.",
            "issues": _issues_payload([i for i in ctx.issues if i.level == "error"]),
        }, ok=False)
    return ToolOutcome({"ok": True}, ok=True, terminal=True, finish=parsed,
                       suggestions=parsed.suggestions)


_HANDLERS = {
    "replace_flow": _replace_flow,
    "apply_edits": _apply_edits,
    "simulate": _simulate,
    "ask_user": _ask_user,
    "finish": _finish,
}
