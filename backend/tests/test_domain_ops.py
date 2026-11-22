from __future__ import annotations

import pytest

from app.domain.diff import diff_flows
from app.domain.edits import apply_edits
from app.domain.engine import ApiMock, Engine
from tests.helpers import buyer_seller, flow


def test_batch_is_atomic():
    f = flow()
    new, errors = apply_edits(f, [
        {"op": "add_node", "node": {"id": "w", "type": "wait", "duration_seconds": 3600}},
        {"op": "connect", "from": "w", "handle": "next", "to": "nope"},
    ])
    assert [e.index for e in errors] == [1]
    assert new is f and f.get_node("w") is None


def test_add_duplicate_id_rejected():
    node = {"id": "retry", "type": "send_message", "text": "x"}
    _, errors = apply_edits(flow(), [{"op": "add_node", "node": node}])
    assert errors and "already exists" in errors[0].message


def test_update_merges_and_forbids_type_change():
    new, errors = apply_edits(flow(), [{"op": "update_node", "id": "send_article",
                                        "changes": {"text": "new"}}])
    assert not errors and new.get_node("send_article").text == "new"
    _, errors = apply_edits(flow(), [{"op": "update_node", "id": "send_article",
                                      "changes": {"type": "wait"}}])
    assert errors


def test_remove_node_removes_edges():
    new, errors = apply_edits(flow(), [{"op": "remove_node", "id": "retry"}])
    assert not errors
    assert all("retry" not in (e.from_, e.to) for e in new.edges)


def test_connect_replaces_and_checks_handle():
    new, errors = apply_edits(flow(), [{"op": "connect", "from": "ask_role",
                                        "handle": "fallback", "to": "assign_sales"}])
    assert not errors and new.edge_from("ask_role", "fallback").to == "assign_sales"
    assert len([e for e in new.edges if e.key == "ask_role:fallback"]) == 1
    _, errors = apply_edits(flow(), [{"op": "connect", "from": "ask_role",
                                      "handle": "option:Nope", "to": "retry"}])
    assert errors


def test_unknown_op_reports_index():
    _, errors = apply_edits(flow(), [{"op": "set_start", "node_id": "retry"}, {"op": "explode"}])
    assert [e.index for e in errors] == [1]


def test_set_start_only_gives_single_start_change():
    before = flow()
    after, _ = apply_edits(before, [{"op": "set_start", "node_id": "retry"}])
    changes = diff_flows(before, after)
    assert len(changes) == 1
    c = changes[0]
    assert (c.kind, c.target, c.before, c.after) == ("changed", "start", "ask_role", "retry")


def test_trigger_change_diff():
    before = flow()
    after, _ = apply_edits(before, [{"op": "set_trigger",
                                     "trigger": {"type": "keyword", "keywords": ["hi"]}}])
    [c] = diff_flows(before, after)
    assert c.target == "trigger" and c.after["keywords"] == ["hi"]


def test_diff_identical_is_empty_and_new_flow_is_all_added():
    assert diff_flows(flow(), flow()) == []
    added = diff_flows(None, flow())
    assert {c.kind for c in added} == {"added"}
    assert sum(c.target == "node" for c in added) == 4


def run(f, *inputs, **kw):
    eng = Engine(f, **kw)
    st = eng.start()
    for i in inputs:
        st = eng.send_input(i)
    return st


def test_engine_buyer_seller_and_fallback():
    assert run(flow(), "Buyer").status == "assigned"
    st = run(flow(), "seller")
    assert st.status == "completed" and st.transcript[-1].text.startswith("Article")
    st = run(flow(), "banana")
    assert st.status == "waiting_input" and st.current_node_id == "ask_role"
    assert "role" not in st.variables
    assert [t.text for t in st.transcript if t.role == "bot"][-2] == "Please pick an option."


def _api_flow():
    d = {
        "start_node_id": "api",
        "nodes": [
            {"id": "api", "type": "api_call", "url": "https://x.io/{{contact.phone}}",
             "save_as": "order"},
            {"id": "ok", "type": "send_message", "text": "Status: {{order.status}}"},
            {"id": "bad", "type": "send_message", "text": "Try later"},
        ],
        "edges": [{"from": "api", "handle": "success", "to": "ok"},
                  {"from": "api", "handle": "failure", "to": "bad"}],
    }
    return flow(d)


def test_engine_api_success_and_failure():
    st = run(_api_flow(), api_mocks={"api": ApiMock(body={"status": "shipped"})})
    assert st.transcript[-1].text == "Status: shipped"
    st = run(_api_flow(), api_mocks={"api": ApiMock(outcome="failure")})
    assert st.transcript[-1].text == "Try later" and "order" not in st.variables


def test_engine_missing_variable_is_error_not_empty_string():
    st = run(_api_flow())
    assert st.status == "error" and "order.status" in st.error


def test_engine_wait_and_condition():
    d = {
        "start_node_id": "q",
        "nodes": [
            {"id": "q", "type": "ask_question", "text": "tier?", "mode": "text",
             "save_as": "tier"},
            {"id": "w", "type": "wait", "duration_seconds": 3600},
            {"id": "c", "type": "condition", "variable": "tier", "operator": "equals",
             "value": "VIP"},
            {"id": "vip", "type": "send_message", "text": "Hello VIP {{contact.name}}"},
            {"id": "std", "type": "send_message", "text": "Hello"},
        ],
        "edges": [{"from": "q", "handle": "next", "to": "w"},
                  {"from": "w", "handle": "next", "to": "c"},
                  {"from": "c", "handle": "true", "to": "vip"},
                  {"from": "c", "handle": "false", "to": "std"}],
    }
    st = run(flow(d), "  vip ")
    assert st.variables["tier"] == "vip"
    assert st.transcript[-1].text == "Hello VIP Alex"
    assert any("waited 3600s" in t.detail for t in st.trace)
    assert run(flow(d), "basic").transcript[-1].text == "Hello"


def test_engine_max_steps():
    eng = Engine(flow(), max_steps=8)
    st = eng.start()
    while st.status == "waiting_input":
        st = eng.send_input("banana")
    assert st.status == "max_steps" and st.steps == 8


def test_engine_rejects_input_when_not_waiting():
    eng = Engine(flow())
    eng.start()
    eng.send_input("Buyer")
    with pytest.raises(ValueError):
        eng.send_input("again")


def test_assign_team_with_followup_continues():
    d = buyer_seller()
    d["nodes"].append({"id": "bye", "type": "send_message", "text": "A colleague will reply."})
    d["edges"].append({"from": "assign_sales", "handle": "next", "to": "bye"})
    st = run(flow(d), "Buyer")
    assert st.status == "completed" and st.assigned_team == "Sales"
