from __future__ import annotations

from app.domain.engine import Engine
from app.domain.text import match_option, normalize
from app.domain.validator import validate_flow
from tests.helpers import flow


def _merge_flow(seller_saves_email: bool, buyer_saves_email: bool) -> dict:
    def ask(nid: str, save: bool) -> dict:
        n = {"id": nid, "type": "ask_question", "text": "email?", "mode": "text"}
        if save:
            n["save_as"] = "email"
        return n

    return {
        "start_node_id": "role",
        "nodes": [
            {"id": "role", "type": "ask_question", "text": "?", "options": ["Buyer", "Seller"]},
            ask("buyer_q", buyer_saves_email),
            ask("seller_q", seller_saves_email),
            {"id": "done", "type": "send_message", "text": "Sent to {{email}}"},
            {"id": "oops", "type": "send_message", "text": "?"},
        ],
        "edges": [
            {"from": "role", "handle": "option:Buyer", "to": "buyer_q"},
            {"from": "role", "handle": "option:Seller", "to": "seller_q"},
            {"from": "role", "handle": "fallback", "to": "oops"},
            {"from": "buyer_q", "handle": "next", "to": "done"},
            {"from": "seller_q", "handle": "next", "to": "done"},
        ],
    }


def undefined(d: dict) -> list[str]:
    return [i.node_id for i in validate_flow(flow(d)) if i.code == "undefined_variable"]


def test_merge_where_only_one_branch_sets_var_is_error():
    assert undefined(_merge_flow(seller_saves_email=True, buyer_saves_email=False)) == ["done"]


def test_merge_where_both_branches_set_var_is_ok():
    assert undefined(_merge_flow(True, True)) == []


def test_var_used_after_fallback_is_error():
    d = {
        "start_node_id": "q",
        "nodes": [
            {"id": "q", "type": "ask_question", "text": "?", "options": ["A"], "save_as": "pick"},
            {"id": "m", "type": "send_message", "text": "You chose {{pick}}"},
        ],
        "edges": [{"from": "q", "handle": "option:A", "to": "m"},
                  {"from": "q", "handle": "fallback", "to": "m"}],
    }
    assert undefined(d) == ["m"]


def test_var_used_after_api_failure_is_error():
    d = {
        "start_node_id": "api",
        "nodes": [
            {"id": "api", "type": "api_call", "url": "https://x.io", "save_as": "order"},
            {"id": "m", "type": "send_message", "text": "Status {{order.status}}"},
        ],
        "edges": [{"from": "api", "handle": "success", "to": "m"},
                  {"from": "api", "handle": "failure", "to": "m"}],
    }
    assert undefined(d) == ["m"]
    d["nodes"].append({"id": "f", "type": "send_message", "text": "API down"})
    d["edges"][1]["to"] = "f"
    assert undefined(d) == []


def test_contact_vars_always_defined():
    d = {"start_node_id": "m",
         "nodes": [{"id": "m", "type": "send_message", "text": "Hi {{contact.name}}"}]}
    assert undefined(d) == []


def test_condition_variable_is_checked():
    d = {"start_node_id": "c",
         "nodes": [{"id": "c", "type": "condition", "variable": "tier", "value": "VIP"},
                   {"id": "m", "type": "send_message", "text": "x"}],
         "edges": [{"from": "c", "handle": "true", "to": "m"},
                   {"from": "c", "handle": "false", "to": "m"}]}
    assert undefined(d) == ["c"]


def test_loop_converges():
    d = {
        "start_node_id": "q",
        "nodes": [
            {"id": "q", "type": "ask_question", "text": "name?", "mode": "text",
             "save_as": "name"},
            {"id": "greet", "type": "send_message", "text": "Hi {{name}}"},
            {"id": "again", "type": "ask_question", "text": "again?", "options": ["Yes", "No"]},
            {"id": "bye", "type": "send_message", "text": "Bye {{name}}"},
        ],
        "edges": [
            {"from": "q", "handle": "next", "to": "greet"},
            {"from": "greet", "handle": "next", "to": "again"},
            {"from": "again", "handle": "option:Yes", "to": "q"},
            {"from": "again", "handle": "option:No", "to": "bye"},
            {"from": "again", "handle": "fallback", "to": "bye"},
        ],
    }
    assert undefined(d) == []


def test_loop_where_var_set_after_use_is_error():
    d = {
        "start_node_id": "m",
        "nodes": [
            {"id": "m", "type": "send_message", "text": "x={{x}}"},
            {"id": "q", "type": "ask_question", "text": "?", "mode": "text", "save_as": "x"},
        ],
        "edges": [{"from": "m", "handle": "next", "to": "q"},
                  {"from": "q", "handle": "next", "to": "m"}],
    }
    assert undefined(d) == ["m"]


def test_normalize():
    assert normalize("  Hello   World ") == "hello world"


def test_text_and_index_store_same_original_value():
    opts = ["Buyer", "Seller"]
    assert match_option(opts, "1") == "Buyer"
    assert match_option(opts, "  buyer ") == "Buyer"
    assert match_option(opts, "3") is None
    assert match_option(opts, "banana") is None


def test_numeric_option_text_beats_index():
    assert match_option(["2", "1"], "1") == "1"
    assert match_option(["2", "1"], "2") == "2"


def test_engine_saves_original_option_text_for_index_input():
    from tests.helpers import buyer_seller

    for reply in ("1", "BUYER"):
        eng = Engine(flow(buyer_seller()))
        eng.start()
        st = eng.send_input(reply)
        assert st.variables["role"] == "Buyer"
        assert st.status == "assigned"
