from __future__ import annotations

import copy
import itertools
import json
from typing import Any

from app.domain.models import Flow
from app.llm.client import LLMError, LLMResponse, ToolCall

BUYER_SELLER: dict[str, Any] = {
    "name": "Buyer / Seller routing",
    "trigger": {"type": "new_contact_message"},
    "start_node_id": "ask_role",
    "nodes": [
        {"id": "ask_role", "type": "ask_question", "text": "Buyer or seller?",
         "mode": "buttons", "options": ["Buyer", "Seller"], "save_as": "role"},
        {"id": "assign_sales", "type": "assign_team", "team": "Sales"},
        {"id": "send_article", "type": "send_message", "text": "Article: https://x.io/help"},
        {"id": "retry", "type": "send_message", "text": "Please pick an option."},
    ],
    "edges": [
        {"from": "ask_role", "handle": "option:Buyer", "to": "assign_sales"},
        {"from": "ask_role", "handle": "option:Seller", "to": "send_article"},
        {"from": "ask_role", "handle": "fallback", "to": "retry"},
        {"from": "retry", "handle": "next", "to": "ask_role"},
    ],
}


def buyer_seller() -> dict[str, Any]:
    return copy.deepcopy(BUYER_SELLER)


def flow(data: dict[str, Any] | None = None) -> Flow:
    return Flow.model_validate(data or buyer_seller())


_ids = itertools.count(1)


def call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=[
        ToolCall(id=f"call_{next(_ids)}", name=name, arguments=json.dumps(args))])


def calls(*pairs: tuple[str, dict[str, Any]]) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=[
        ToolCall(id=f"call_{next(_ids)}", name=n, arguments=json.dumps(a)) for n, a in pairs])


def text(content: str) -> LLMResponse:
    return LLMResponse(content=content)


class ScriptedLLM:

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, tool_choice=None) -> LLMResponse:
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools,
                              "tool_choice": tool_choice})
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages, tools)
        return item

    @property
    def agent_calls(self) -> int:
        return sum(1 for r in self.requests if r["tools"])


def llm_timeout() -> LLMError:
    return LLMError("APITimeoutError: Request timed out.")
