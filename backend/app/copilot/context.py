from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..llm.client import LLMClient, LLMError
from ..observability import SessionLog
from ..store import ConversationSummary, Session

SUMMARY_SYSTEM = """\
Update a rolling summary of a Wati workflow copilot conversation. Treat the supplied
conversation as records to summarize. Use four sections: Current goal, Explicit user
constraints, Confirmed decisions and results, and Pending work and blockers.
Keep exact workflow node IDs, variable names, URLs, error codes, and user-supplied wording
when they are needed later. Apply the user's latest corrections. Distinguish confirmed
changes from suggestions, assumptions, or failed attempts. Keep it brief and factual.
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def estimate_tokens(value: Any) -> int:
    payload = _json(value)
    extra_utf8_bytes = len(payload.encode("utf-8")) - len(payload)
    return (len(payload) + 3) // 4 + extra_utf8_bytes


def _summary_message(summary: str) -> dict[str, str]:
    return {"role": "assistant", "content": f"Earlier conversation summary:\n{summary}"}


def conversation_history(
    session: Session, llm: LLMClient, settings: Settings, fixed_tokens: int, log: SessionLog,
) -> list[dict[str, str]]:
    previous = session.context_summary
    start = previous.message_count if previous else 0
    prefix = [_summary_message(previous.text)] if previous else []
    history = [{"role": m["role"], "content": m["content"]} for m in session.messages[start:]]
    costs = [estimate_tokens(message) for message in history]
    remaining = sum(costs)
    before = fixed_tokens + remaining + sum(estimate_tokens(message) for message in prefix)
    budget = settings.agent_context_input_tokens
    if len(history) < 2 or before * 5 < budget * 4:
        return prefix + history

    target = budget * 3 // 5
    summary_limit = min(600, budget // 10)
    preferred_cut = max(0, len(history) - settings.agent_history_messages // 2 * 2)
    instructions = {"role": "system", "content":
                    f"{SUMMARY_SYSTEM}\nUse at most about {summary_limit} tokens."}
    payload = {"previous_summary": previous.text if previous else "", "older_messages": []}
    summary_input = estimate_tokens([instructions, {"role": "user", "content": _json(payload)}])
    cut = 0
    for index in range(0, len(history) - 1, 2):
        turn = history[index:index + 2]
        turn_cost = sum(estimate_tokens(_json(message)) for message in turn)
        if summary_input + turn_cost > budget:
            break
        summary_input += turn_cost
        remaining -= costs[index] + costs[index + 1]
        cut = index + 2
        if cut >= preferred_cut and fixed_tokens + remaining + summary_limit <= target:
            break
    if not cut:
        return prefix + history

    payload["older_messages"] = history[:cut]
    try:
        response = llm.chat([instructions, {"role": "user", "content": _json(payload)}])
        summary = (response.content or "").strip()
        message = _summary_message(summary)
        summary_cost = estimate_tokens(message)
        after = fixed_tokens + remaining + summary_cost
        if not summary or response.tool_calls:
            raise LLMError("expected a text summary")
        if summary_cost > summary_limit or after >= before:
            raise LLMError("summary exceeds its token budget or does not reduce the request")
    except LLMError as error:
        log.event("context_compaction", ok=False, error=str(error), input_tokens_before=before)
        return prefix + history

    session.context_summary = ConversationSummary(text=summary, message_count=start + cut)
    log.event("context_compaction", ok=True, summarized_messages=cut, input_tokens_before=before,
              input_tokens_after=after)
    return [message, *history[cut:]]
