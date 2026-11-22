from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from .models import (
    ApiCallNode,
    AskQuestionNode,
    AssignTeamNode,
    ConditionNode,
    Node,
    SendMessageNode,
    WaitNode,
)
from .text import template_vars

CONTACT_VARS = frozenset({"contact.name", "contact.phone"})
WAIT_MIN_SECONDS = 1
WAIT_MAX_SECONDS = 86_400
MAX_OPTIONS = 3


@dataclass(frozen=True)
class NodeSpec:
    type: str
    label: str
    purpose: str
    fields: dict[str, str]
    handles_doc: str
    can_terminate: bool
    example: dict
    handles: Callable[[Node], list[str]]
    writes: Callable[[Node, str], set[str]]
    reads: Callable[[Node], set[str]]


def _none_writes(_n: Node, _h: str) -> set[str]:
    return set()


def _ask_handles(n: Node) -> list[str]:
    assert isinstance(n, AskQuestionNode)
    if n.mode == "text":
        return ["next"]
    return [f"option:{o}" for o in n.options] + ["fallback"]


def _ask_writes(n: Node, h: str) -> set[str]:
    assert isinstance(n, AskQuestionNode)
    if not n.save_as or h == "fallback":
        return set()
    return {n.save_as}


def _api_writes(n: Node, h: str) -> set[str]:
    assert isinstance(n, ApiCallNode)
    return {n.save_as} if (n.save_as and h == "success") else set()


def _api_reads(n: Node) -> set[str]:
    assert isinstance(n, ApiCallNode)
    out = set(template_vars(n.url))
    if isinstance(n.body, str):
        out |= set(template_vars(n.body))
    elif isinstance(n.body, dict):
        out |= set(template_vars(json.dumps(n.body)))
    return out


REGISTRY: dict[str, NodeSpec] = {
    "send_message": NodeSpec(
        type="send_message",
        label="Send Message",
        purpose="Send a text message to the contact. Supports {{variable}} placeholders.",
        fields={"text": "message text (required)"},
        handles_doc="`next` (optional — no outgoing edge means the flow ends here)",
        can_terminate=True,
        example={"id": "send_article", "type": "send_message",
                 "text": "Here is our help article: https://example.com/help"},
        handles=lambda n: ["next"],
        writes=_none_writes,
        reads=lambda n: set(template_vars(n.text)),
    ),
    "ask_question": NodeSpec(
        type="ask_question",
        label="Ask Question",
        purpose="Ask the contact a question and wait for the reply.",
        fields={
            "text": "question text (required)",
            "mode": '"buttons" (pick from options) or "text" (free-text reply)',
            "options": f"buttons mode only: 1-{MAX_OPTIONS} distinct option labels",
            "save_as": "optional variable name to store the reply",
        },
        handles_doc=(
            "buttons mode: one `option:<exact option text>` per option PLUS `fallback` "
            "(reply matched nothing) — ALL required. text mode: `next` (required). "
            "save_as is written on option/next exits, NOT on fallback."
        ),
        can_terminate=False,
        example={"id": "ask_role", "type": "ask_question", "text": "Are you a buyer or a seller?",
                 "mode": "buttons", "options": ["Buyer", "Seller"], "save_as": "role"},
        handles=_ask_handles,
        writes=_ask_writes,
        reads=lambda n: set(template_vars(n.text)),
    ),
    "condition": NodeSpec(
        type="condition",
        label="Condition",
        purpose="Branch on a single rule over a variable (case-insensitive comparison).",
        fields={
            "variable": "variable name, e.g. role or contact.name",
            "operator": "equals | not_equals | contains",
            "value": "value to compare with",
        },
        handles_doc="`true` and `false` — both required",
        can_terminate=False,
        example={"id": "is_vip", "type": "condition", "variable": "tier",
                 "operator": "equals", "value": "VIP"},
        handles=lambda n: ["true", "false"],
        writes=_none_writes,
        reads=lambda n: {n.variable},
    ),
    "api_call": NodeSpec(
        type="api_call",
        label="API Call",
        purpose="Call an external HTTP API (mocked in simulation).",
        fields={
            "method": "GET | POST",
            "url": "https URL; may contain {{variable}}",
            "body": "optional JSON object or string",
            "save_as": "optional variable name for the response body",
        },
        handles_doc="`success` and `failure` — both required. save_as is written only on success.",
        can_terminate=False,
        example={"id": "lookup_order", "type": "api_call", "method": "GET",
                 "url": "https://api.example.com/orders?phone={{contact.phone}}",
                 "save_as": "order"},
        handles=lambda n: ["success", "failure"],
        writes=_api_writes,
        reads=_api_reads,
    ),
    "assign_team": NodeSpec(
        type="assign_team",
        label="Assign to Team",
        purpose="Hand the conversation over to a human team from the workspace.",
        fields={"team": "team name — MUST be one of the workspace teams"},
        handles_doc="`next` (optional — usually the flow ends here)",
        can_terminate=True,
        example={"id": "assign_sales", "type": "assign_team", "team": "Sales"},
        handles=lambda n: ["next"],
        writes=_none_writes,
        reads=lambda n: set(),
    ),
    "wait": NodeSpec(
        type="wait",
        label="Wait",
        purpose="Pause the flow for a while before continuing.",
        fields={"duration_seconds": f"integer, {WAIT_MIN_SECONDS}-{WAIT_MAX_SECONDS}"},
        handles_doc="`next` — required",
        can_terminate=False,
        example={"id": "wait_1h", "type": "wait", "duration_seconds": 3600},
        handles=lambda n: ["next"],
        writes=_none_writes,
        reads=lambda n: set(),
    ),
}

assert set(REGISTRY) == {
    c.model_fields["type"].default
    for c in (SendMessageNode, AskQuestionNode, ConditionNode, ApiCallNode, AssignTeamNode,
              WaitNode)
}


def spec_for(node: Node) -> NodeSpec:
    return REGISTRY[node.type]


def node_catalog_markdown() -> str:
    lines = []
    for s in REGISTRY.values():
        lines.append(f"### `{s.type}` ({s.label})")
        lines.append(s.purpose)
        for f, d in s.fields.items():
            lines.append(f"- `{f}`: {d}")
        lines.append(f"- exits: {s.handles_doc}")
        lines.append(f"- can end the flow: {'yes' if s.can_terminate else 'no'}")
        lines.append(f"- example: `{json.dumps(s.example, ensure_ascii=False)}`")
        lines.append("")
    return "\n".join(lines)
