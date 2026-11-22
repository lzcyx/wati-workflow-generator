from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import Settings
from ..domain.diff import Change, diff_flows
from ..domain.models import DEFAULT_WORKSPACE, Flow, Workspace
from ..domain.validator import Issue, validate_flow
from ..llm.client import LLMClient, LLMError
from ..observability import SessionLog
from ..store import Session
from .context import conversation_history, estimate_tokens
from .prompts import EXPLAIN_SYSTEM, build_system_prompt, flow_context_message
from .tools import TOOL_SCHEMAS, ToolContext, ToolOutcome, run_tool

TurnStatus = Literal["completed", "partial", "declined", "clarification", "failed"]
TurnUpdate = Callable[[dict[str, Any]], None]

NUDGE = ("You must respond by calling one of the tools. End the turn with `finish` "
         "or `ask_user`.")


class TraceStep(BaseModel):
    step: int
    tool: str | None
    args_summary: str | None = None
    ok: bool
    result_summary: str | None = None
    errors: int = 0
    warnings: int = 0
    duration_ms: int


class TurnResult(BaseModel):
    status: TurnStatus
    reply: str
    flow: dict[str, Any] | None
    version: int | None
    diff: list[Change] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    explanation: str | None = None
    explanation_source: Literal["llm", "fallback"] | None = None
    assumptions: list[str] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    draft: dict[str, Any] | None = None
    trace_id: str
    trace: list[TraceStep] = Field(default_factory=list)


def _short(obj: Any, n: int = 200) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


class Agent:
    def __init__(self, llm: LLMClient, settings: Settings,
                 workspace: Workspace = DEFAULT_WORKSPACE, log_dir: Path | None = None):
        self.llm = llm
        self.settings = settings
        self.workspace = workspace
        self.log_dir = log_dir


    def run_turn(
        self, session: Session, user_text: str, on_update: TurnUpdate | None = None,
    ) -> TurnResult:
        with session.lock:
            trace_id = f"t_{uuid.uuid4().hex[:12]}"
            log = SessionLog(self.log_dir, session.id, trace_id)
            t_turn = time.perf_counter()
            result = self._run(session, user_text, log, trace_id, on_update)
            session.messages.append({"role": "user", "content": user_text})
            session.messages.append({"role": "assistant", "content": result.reply,
                                     "status": result.status, "trace_id": trace_id,
                                     "version": result.version})
            log.event("turn_end", status=result.status, version=result.version,
                      reply=result.reply, diff=[c.model_dump() for c in result.diff],
                      assumptions=result.assumptions, unsupported=result.unsupported,
                      suggestions=result.suggestions,
                      explanation_source=result.explanation_source,
                      llm_steps=len(result.trace),
                      duration_ms=int((time.perf_counter() - t_turn) * 1000))
            return result

    def _run(self, session: Session, user_text: str, log: SessionLog,
             trace_id: str, on_update: TurnUpdate | None) -> TurnResult:
        base = session.current
        base_version = session.current_version
        base_dump = base.dump() if base else None
        ctx = ToolContext(base=base, workspace=self.workspace,
                          sim_max_steps=self.settings.sim_max_steps)

        messages = self._build_messages(session, user_text, base, base_version, log)
        log.event("turn_start", user_message=user_text, base_version=base_version,
                  model=getattr(getattr(self.llm, "settings", None), "llm_model", None),
                  messages=messages)

        def unchanged(status: TurnStatus, reply: str, **kw: Any) -> TurnResult:
            if "issues" not in kw:
                kw["issues"] = validate_flow(base, self.workspace) if base else []
            return TurnResult(status=status, reply=reply, flow=base_dump, version=base_version,
                              trace_id=trace_id, trace=trace, **kw)

        trace: list[TraceStep] = []

        def record_step(step: TraceStep) -> None:
            trace.append(step)
            if on_update:
                on_update({"type": "step", "step": step.model_dump(mode="json")})

        outcome: ToolOutcome | None = None
        tool_choice = self.settings.llm_tool_choice or None
        max_steps = self.settings.agent_max_iterations

        for step in range(1, max_steps + 1):
            t0 = time.perf_counter()
            try:
                resp = self.llm.chat(messages, tools=TOOL_SCHEMAS, tool_choice=tool_choice)
            except LLMError as e:
                ms = int((time.perf_counter() - t0) * 1000)
                log.event("llm_call", step=step, ok=False, error=str(e), duration_ms=ms)
                record_step(TraceStep(step=step, tool=None, ok=False, duration_ms=ms,
                                       result_summary=f"LLM error: {_short(str(e))}"))
                return unchanged("failed", f"The AI service failed ({e}). Your flow was not "
                                           "changed — please try again.")
            llm_ms = int((time.perf_counter() - t0) * 1000)
            log.event("llm_call", step=step, ok=True, duration_ms=llm_ms,
                      content=resp.content, usage=resp.usage,
                      tool_calls=[{"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                  for tc in resp.tool_calls])
            messages.append(resp.to_message())

            if not resp.tool_calls:
                messages.append({"role": "user", "content": NUDGE})
                record_step(TraceStep(step=step, tool=None, ok=False, duration_ms=llm_ms,
                                       result_summary="no tool call; nudged"))
                continue

            for tc in resp.tool_calls:
                t1 = time.perf_counter()
                try:
                    args = json.loads(tc.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError as e:
                    out = ToolOutcome({"error": f"arguments are not valid JSON: {e}"}, ok=False)
                    args = {"_raw": tc.arguments}
                else:
                    out = run_tool(ctx, tc.name, args)
                tool_ms = int((time.perf_counter() - t1) * 1000)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(out.result, ensure_ascii=False)})
                n_err = sum(i.level == "error" for i in ctx.issues)
                n_warn = sum(i.level == "warning" for i in ctx.issues)
                log.event("tool_call", step=step, tool=tc.name, args=args, ok=out.ok,
                          result=out.result, draft_errors=n_err, draft_warnings=n_warn,
                          duration_ms=tool_ms)
                record_step(TraceStep(
                    step=step, tool=tc.name, args_summary=_short(args), ok=out.ok,
                    result_summary=_short(out.result.get("error") or "ok"),
                    errors=n_err, warnings=n_warn, duration_ms=llm_ms + tool_ms))
                if out.terminal:
                    outcome = out
                    break
            if outcome is not None:
                break

        if outcome is None:
            return unchanged(
                "failed",
                "I couldn't finish this change within the step limit. Your flow was not changed.",
                draft=ctx.draft.dump() if ctx.draft else None,
                issues=ctx.issues)

        if outcome.questions is not None:
            return unchanged("clarification", "\n".join(outcome.questions),
                             questions=outcome.questions, suggestions=outcome.suggestions)

        fin = outcome.finish
        assert fin is not None
        changes = diff_flows(base, ctx.draft) if ctx.draft is not None else []

        if not changes:
            status: TurnStatus = "declined" if fin.unsupported else "completed"
            return unchanged(status, fin.summary, assumptions=fin.assumptions,
                             unsupported=fin.unsupported, suggestions=fin.suggestions)

        assert ctx.draft is not None
        new = ctx.draft.model_copy(deep=True)
        new.id = session.flow_id
        new.version = (base_version or 0) + 1
        session.versions.append(new)
        log.event("commit", version=new.version, flow=new.dump(),
                  diff=[c.model_dump() for c in changes])

        result = TurnResult(
            status="partial" if fin.unsupported else "completed",
            reply=fin.summary, flow=new.dump(), version=new.version, diff=changes,
            issues=validate_flow(new, self.workspace), assumptions=fin.assumptions,
            unsupported=fin.unsupported, suggestions=fin.suggestions,
            trace_id=trace_id, trace=trace)
        on_text: Callable[[str], None] | None = None
        if on_update:
            on_update({"type": "reply", "turn": result.model_dump(mode="json")})

            def publish_explanation(text: str) -> None:
                on_update({"type": "explanation", "delta": text})

            on_text = publish_explanation

        result.explanation, result.explanation_source = self._explain(
            new, changes, fin.summary, log, on_text,
        )
        return result

    def _build_messages(self, session: Session, user_text: str, base: Flow | None,
                        version: int | None, log: SessionLog) -> list[dict[str, Any]]:
        system = {"role": "system", "content": build_system_prompt(self.workspace)}
        current = {"role": "user", "content": f"{flow_context_message(base, version)}\n\n"
                   f"User message:\n{user_text}"}
        fixed_tokens = estimate_tokens([system, current, TOOL_SCHEMAS])
        history = conversation_history(session, self.llm, self.settings, fixed_tokens, log)
        return [system, *history, current]

    def _explain(
        self, flow: Flow, changes: list[Change], summary: str, log: SessionLog,
        on_text: Callable[[str], None] | None = None,
    ) -> tuple[str, Literal["llm", "fallback"]]:
        t0 = time.perf_counter()
        data = flow.dump()
        payload = {
            "flow": {k: data[k] for k in ("name", "trigger", "start_node_id", "nodes", "edges")},
            "changes": [c.model_dump(exclude_none=True) for c in changes],
        }
        messages = [
            {"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            resp = None
            stream_text = getattr(self.llm, "stream_text", None)
            if on_text and callable(stream_text):
                try:
                    resp = stream_text(messages, on_text)
                except LLMError:
                    pass
            if resp is None or not (resp.content or "").strip():
                resp = self.llm.chat(messages)
            text = (resp.content or "").strip()
            if not text:
                raise LLMError("empty explanation")
            log.event("explanation", source="llm", text=text, usage=resp.usage,
                      duration_ms=int((time.perf_counter() - t0) * 1000))
            return text, "llm"
        except Exception as e:
            text = _fallback_explanation(summary, changes)
            log.event("explanation", source="fallback", error=str(e), text=text,
                      duration_ms=int((time.perf_counter() - t0) * 1000))
            return text, "fallback"


def _fallback_explanation(summary: str, changes: list[Change]) -> str:
    counts: dict[str, int] = {}
    for c in changes:
        k = f"{c.target} {c.kind}"
        counts[k] = counts.get(k, 0) + 1
    detail = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    return f"{summary}\n\nChanges: {detail}."
