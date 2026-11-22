from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.copilot.agent import Agent
from app.store import ConversationSummary, Store
from tests.helpers import ScriptedLLM, buyer_seller, call, calls, llm_timeout, text

EXPLAIN = text("This flow asks new contacts whether they buy or sell.")


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, llm_api_key="x", llm_model="fake",
                    agent_max_iterations=4, log_dir=str(tmp_path))


def make(settings, script):
    llm = ScriptedLLM(script)
    agent = Agent(llm, settings, log_dir=settings.log_path)
    session = Store().create_session()
    return agent, session, llm


def seed(agent, session, llm):
    llm.script[:0] = [call("replace_flow", flow=buyer_seller()),
                      call("finish", summary="Built it."), EXPLAIN]
    r = agent.run_turn(session, "buyer seller flow")
    assert r.status == "completed" and r.version == 1
    return r


def test_row1_llm_failure_first_turn(settings):
    agent, s, llm = make(settings, [llm_timeout()])
    r = agent.run_turn(s, "hi")
    assert (r.status, r.flow, r.version) == ("failed", None, None)
    assert s.versions == []


def test_row1_llm_failure_mid_loop_keeps_previous_version(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    llm.script += [call("apply_edits", ops=[{"op": "remove_node", "id": "retry"}]),
                   llm_timeout()]
    r = agent.run_turn(s, "remove retry")
    assert r.status == "failed" and r.version == 1 and len(s.versions) == 1
    assert any(n["id"] == "retry" for n in r.flow["nodes"])


def test_row2_steps_exhausted_with_valid_draft_does_not_commit(settings):
    script = [call("replace_flow", flow=buyer_seller())] + \
             [call("simulate", scenarios=[{"inputs": ["Buyer"]}])] * 3
    agent, s, llm = make(settings, script)
    r = agent.run_turn(s, "build it")
    assert r.status == "failed" and r.flow is None and r.version is None
    assert r.draft is not None and r.draft["start_node_id"] == "ask_role"
    assert s.versions == [] and llm.agent_calls == 4


def test_row3_first_turn_clarification_returns_null_flow(settings):
    agent, s, llm = make(settings, [call("ask_user", questions=["What is the article URL?"])])
    r = agent.run_turn(s, "send sellers our help article")
    assert r.status == "clarification" and r.flow is None and r.version is None
    assert r.questions == ["What is the article URL?"] and r.explanation is None


def test_row3_clarification_discards_draft(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    llm.script += [call("apply_edits", ops=[{"op": "remove_node", "id": "retry"}]),
                   call("ask_user", questions=["Sure?"])]
    r = agent.run_turn(s, "maybe remove retry")
    assert r.status == "clarification" and r.version == 1 and r.diff == []
    assert len(s.versions) == 1


def test_row4_completed_commits_and_explains(settings):
    agent, s, llm = make(settings, [])
    r = seed(agent, s, llm)
    assert r.explanation_source == "llm" and r.explanation.startswith("This flow")
    assert r.flow["id"] == s.flow_id and r.flow["version"] == 1
    assert any(c.target == "node" and c.kind == "added" for c in r.diff)


def test_row5_partial(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    llm.script += [
        call("apply_edits", ops=[{"op": "update_node", "id": "send_article",
                                  "changes": {"text": "New article text"}}]),
        call("finish", summary="Updated text; email not possible.",
             unsupported=["Sending an email"]),
        EXPLAIN,
    ]
    r = agent.run_turn(s, "change text and email them")
    assert r.status == "partial" and r.version == 2
    assert r.unsupported == ["Sending an email"]
    assert [(c.kind, c.id) for c in r.diff] == [("changed", "send_article")]


def test_row6_declined(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    llm.script += [call("finish", summary="Can't send email.", unsupported=["email"])]
    r = agent.run_turn(s, "email everyone")
    assert r.status == "declined" and r.version == 1 and r.diff == [] and len(s.versions) == 1


def test_row6_declined_first_turn_flow_null(settings):
    agent, s, llm = make(settings, [call("finish", summary="No.", unsupported=["email"])])
    r = agent.run_turn(s, "email everyone")
    assert r.status == "declined" and r.flow is None


def test_row7_question_only_completed_without_new_version(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    n_requests = len(llm.requests)
    llm.script += [call("finish", summary="It routes buyers to Sales.")]
    r = agent.run_turn(s, "what does this flow do?")
    assert r.status == "completed" and r.version == 1 and r.diff == []
    assert r.explanation is None and len(s.versions) == 1
    assert len(llm.requests) == n_requests + 1


def test_finish_rejected_then_fixed(settings):
    broken = buyer_seller()
    broken["nodes"][1]["team"] = "VIP"
    agent, s, llm = make(settings, [
        call("replace_flow", flow=broken),
        call("finish", summary="done"),
        call("apply_edits", ops=[{"op": "update_node", "id": "assign_sales",
                                  "changes": {"team": "Sales"}}]),
        call("finish", summary="done for real"),
        EXPLAIN,
    ])
    r = agent.run_turn(s, "route vip")
    assert r.status == "completed" and r.version == 1 and r.reply == "done for real"
    tool_msgs = [m for m in llm.requests[2]["messages"] if m["role"] == "tool"]
    assert "finish rejected" in tool_msgs[-1]["content"]
    assert "unknown_team" in tool_msgs[-1]["content"]
    assert [t.tool for t in r.trace] == ["replace_flow", "finish", "apply_edits", "finish"]


def test_validation_issues_returned_with_write(settings):
    broken = buyer_seller()
    broken["edges"] = broken["edges"][:2]
    agent, s, llm = make(settings, [call("replace_flow", flow=broken),
                                    call("ask_user", questions=["?"])])
    agent.run_turn(s, "x")
    result = json.loads(llm.requests[1]["messages"][-1]["content"])
    assert any(i["code"] == "unconnected_exit" for i in result["issues"])


def test_explanation_timeout_falls_back_and_commits_once(settings):
    agent, s, llm = make(settings, [call("replace_flow", flow=buyer_seller()),
                                    call("finish", summary="Built."), llm_timeout()])
    r = agent.run_turn(s, "build")
    assert r.status == "completed" and r.version == 1 and len(s.versions) == 1
    assert r.explanation_source == "fallback" and r.explanation.startswith("Built.")


def test_bad_json_unknown_tool_and_no_tool_call_are_recoverable(settings):
    from app.llm.client import LLMResponse, ToolCall

    bad_json = LLMResponse(content=None,
                           tool_calls=[ToolCall(id="c1", name="replace_flow", arguments="{oops")])
    agent, s, llm = make(settings, [bad_json, call("teleport"), text("hmm"),
                                    call("ask_user", questions=["?"])])
    r = agent.run_turn(s, "x")
    assert r.status == "clarification"
    assert [t.ok for t in r.trace] == [False, False, False, True]
    assert llm.requests[3]["messages"][-1]["content"].startswith("You must respond")


def test_schema_invalid_replace_flow_keeps_draft(settings):
    bad = buyer_seller()
    bad["nodes"][0]["type"] = "send_email"
    agent, s, llm = make(settings, [call("replace_flow", flow=bad),
                                    call("finish", summary="nothing")])
    r = agent.run_turn(s, "x")
    assert r.status == "completed" and r.flow is None
    assert r.trace[0].ok is False


def test_multiple_tool_calls_in_one_response(settings):
    agent, s, llm = make(settings, [
        calls(("replace_flow", {"flow": buyer_seller()}),
              ("simulate", {"scenarios": [{"name": "buyer", "inputs": ["Buyer"]},
                                          {"name": "junk", "inputs": ["??"]}]}),
              ("finish", {"summary": "ok"})),
        EXPLAIN,
    ])
    r = agent.run_turn(s, "x")
    assert r.status == "completed" and r.version == 1
    assert [t.tool for t in r.trace] == ["replace_flow", "simulate", "finish"]
    assert llm.agent_calls == 1
    assert [m["role"] for m in s.messages] == ["user", "assistant"]


def test_simulate_results_shape(settings):
    agent, s, llm = make(settings, [
        call("replace_flow", flow=buyer_seller()),
        call("simulate", scenarios=[{"name": "buyer", "inputs": ["Buyer"]},
                                    {"name": "junk", "inputs": ["??"]}]),
        call("ask_user", questions=["?"]),
    ])
    agent.run_turn(s, "x")
    res = json.loads(llm.requests[2]["messages"][-1]["content"])["results"]
    assert res[0]["status"] == "assigned" and res[0]["assigned_team"] == "Sales"
    assert res[1]["status"] == "waiting_input"


def test_second_turn_sees_history_and_current_flow(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    llm.script += [call("finish", summary="ok")]
    agent.run_turn(s, "anything else?")
    msgs = llm.requests[-1]["messages"]
    assert [m["role"] for m in msgs[:3]] == ["system", "user", "assistant"]
    assert msgs[-1]["content"].startswith("Current flow (version 1)")
    assert '"ask_role"' in msgs[-1]["content"]


def test_context_compaction_rolls_forward_and_keeps_complete_recent_turns(settings):
    settings = settings.model_copy(update={"agent_context_input_tokens": 8_000})
    first_summary = "Current goal: Build the checkout flow."
    updated_summary = "Current goal: Build the checkout flow. Constraint: use Support."
    agent, s, llm = make(settings, [
        text(first_summary), call("finish", summary="First follow-up"),
        text(updated_summary), call("simulate", scenarios=[]), call("finish", summary="Done"),
    ])
    older = [
        {
            "role": "user",
            "content": "Build the checkout flow. " + "Review the checkout rules. " * 600,
        },
        {"role": "assistant", "content": "Understood."},
    ]
    recent = [{"role": role, "content": f"Recent {role} {index}"}
              for index in range(4) for role in ("user", "assistant")]
    s.messages.extend(older + recent)

    assert agent.run_turn(s, "What is next?").status == "completed"
    first_request = llm.requests[0]
    assert first_request["tools"] is None
    assert json.loads(first_request["messages"][-1]["content"]) == {
        "previous_summary": "", "older_messages": older,
    }
    prompt = llm.requests[1]["messages"]
    assert prompt[1]["role"] == "assistant" and first_summary in prompt[1]["content"]
    assert prompt[2:-1] == recent
    assert s.messages[:10] == older + recent
    assert s.context_summary.message_count == 2

    s.messages.extend([
        {
            "role": "user",
            "content": "Use Support. " + "Route requests to Support. " * 600,
        },
        {"role": "assistant", "content": "Understood."},
    ])
    assert agent.run_turn(s, "Continue.").status == "completed"
    second_payload = json.loads(llm.requests[2]["messages"][-1]["content"])
    assert second_payload["previous_summary"] == first_summary
    assert second_payload["older_messages"][0] == recent[0]
    assert len(second_payload["older_messages"]) == s.context_summary.message_count - 2
    assert s.context_summary.text == updated_summary
    tools_prompt = llm.requests[4]["messages"]
    assert tools_prompt[-2]["tool_calls"][0]["id"] == tools_prompt[-1]["tool_call_id"]


@pytest.mark.parametrize("response", [llm_timeout(), text(""), text("x" * 3_000)])
def test_context_compaction_failure_preserves_previous_summary_and_raw_history(settings, response):
    settings = settings.model_copy(update={"agent_context_input_tokens": 8_000})
    agent, s, llm = make(settings, [response, call("finish", summary="Done")])

    previous = ConversationSummary(text="Use Support for escalations.", message_count=2)
    s.context_summary = previous
    s.messages.extend([
        {"role": "user", "content": "Use Support for escalations."},
        {"role": "assistant", "content": "Understood."},
        {
            "role": "user",
            "content": "Add another branch. " + "Keep the existing routing. " * 600,
        },
        {"role": "assistant", "content": "Which branch?"},
    ])
    original = list(s.messages)

    assert agent.run_turn(s, "The billing branch.").status == "completed"
    prompt = llm.requests[1]["messages"]
    assert previous.text in prompt[1]["content"]
    assert prompt[2:-1] == original[2:]
    assert s.context_summary == previous
    assert s.messages[:4] == original


def test_session_log_written(settings):
    agent, s, llm = make(settings, [])
    seed(agent, s, llm)
    path = settings.log_path / "sessions" / f"{s.id}.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds == ["turn_start", "llm_call", "tool_call", "llm_call", "tool_call",
                     "commit", "explanation", "turn_end"]
    assert len({e["trace_id"] for e in events}) == 1
    assert events[-1]["status"] == "completed"
    assert events[2]["tool"] == "replace_flow" and events[2]["args"]["flow"]["name"]


def test_clarification_passes_suggestions(settings):
    agent, s, llm = make(settings, [call("ask_user", questions=["Which team?"],
                                         suggestions=["Use Support", "Use Sales"])])
    r = agent.run_turn(s, "assign VIPs to the VIP team")
    assert r.status == "clarification" and r.suggestions == ["Use Support", "Use Sales"]


def test_finish_suggestions_cleaned_and_capped(settings):
    agent, s, llm = make(settings, [
        call("replace_flow", flow=buyer_seller()),
        call("finish", summary="Built.",
             suggestions=[" Add a wait ", "Add a wait", "", 42, "a", "b", "c"]),
        EXPLAIN,
    ])
    r = agent.run_turn(s, "build")
    assert r.status == "completed" and r.suggestions == ["Add a wait", "a", "b"]


def test_bad_suggestions_never_break_the_turn(settings):
    agent, s, llm = make(settings, [call("finish", summary="ok", suggestions="not a list")])
    r = agent.run_turn(s, "hi")
    assert r.status == "completed" and r.suggestions == []


def test_tool_schemas_offer_suggestions():
    from app.copilot.tools import TOOL_SCHEMAS

    by_name = {t["function"]["name"]: t["function"]["parameters"] for t in TOOL_SCHEMAS}
    assert "suggestions" in by_name["ask_user"]["properties"]
    assert "suggestions" in by_name["finish"]["properties"]
