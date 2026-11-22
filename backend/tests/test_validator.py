from __future__ import annotations

import pytest

from app.domain.models import Flow
from app.domain.validator import validate_flow
from tests.helpers import buyer_seller, flow


def codes(f: Flow, level: str | None = None) -> set[str]:
    return {i.code for i in validate_flow(f) if level is None or i.level == level}


def test_valid_flow_has_no_issues():
    assert validate_flow(flow()) == []


def test_missing_start():
    d = buyer_seller()
    d["start_node_id"] = "nope"
    assert "missing_start" in codes(flow(d), "error")


def test_edge_to_unknown_node():
    d = buyer_seller()
    d["edges"][0]["to"] = "ghost"
    assert "edge_bad_target" in codes(flow(d), "error")


def test_duplicate_node_id():
    d = buyer_seller()
    d["nodes"].append({"id": "retry", "type": "send_message", "text": "dup"})
    assert "duplicate_node_id" in codes(flow(d), "error")


def test_bad_handle():
    d = buyer_seller()
    d["edges"].append({"from": "assign_sales", "handle": "true", "to": "retry"})
    assert "bad_handle" in codes(flow(d), "error")


def test_duplicate_edge_on_same_exit():
    d = buyer_seller()
    d["edges"].append({"from": "retry", "handle": "next", "to": "send_article"})
    assert "duplicate_edge" in codes(flow(d), "error")


def test_unconnected_fallback():
    d = buyer_seller()
    d["edges"] = [e for e in d["edges"] if e["handle"] != "fallback"]
    issues = validate_flow(flow(d))
    assert any(i.code == "unconnected_exit" and i.node_id == "ask_role" for i in issues)


def test_terminal_nodes_may_have_no_exit():
    assert "unconnected_exit" not in codes(flow())


def test_wait_must_be_connected():
    d = buyer_seller()
    d["nodes"].append({"id": "w", "type": "wait", "duration_seconds": 60})
    d["edges"].append({"from": "send_article", "handle": "next", "to": "w"})
    assert "unconnected_exit" in codes(flow(d), "error")


def test_loop_without_question_is_error():
    d = {
        "start_node_id": "a",
        "nodes": [{"id": "a", "type": "send_message", "text": "a"},
                  {"id": "b", "type": "wait", "duration_seconds": 5}],
        "edges": [{"from": "a", "handle": "next", "to": "b"},
                  {"from": "b", "handle": "next", "to": "a"}],
    }
    assert "infinite_loop" in codes(flow(d), "error")


def test_loop_through_question_is_ok():
    assert "infinite_loop" not in codes(flow())


@pytest.mark.parametrize("options,ok", [([], False), (["a"], True), (["a", "b", "c"], True),
                                        (["a", "b", "c", "d"], False)])
def test_option_count(options, ok):
    d = {"start_node_id": "q",
         "nodes": [{"id": "q", "type": "ask_question", "text": "?", "options": options},
                   {"id": "m", "type": "send_message", "text": "x"}],
         "edges": [{"from": "q", "handle": f"option:{o}", "to": "m"} for o in options]
         + [{"from": "q", "handle": "fallback", "to": "m"}]}
    assert ("option_count" not in codes(flow(d))) is ok


def test_duplicate_options_after_normalization():
    d = buyer_seller()
    d["nodes"][0]["options"] = ["Buyer", " buyer"]
    assert "duplicate_option" in codes(flow(d), "error")


@pytest.mark.parametrize("secs,ok", [(0, False), (1, True), (86400, True), (86401, False)])
def test_wait_range(secs, ok):
    d = buyer_seller()
    d["nodes"].append({"id": "w", "type": "wait", "duration_seconds": secs})
    d["edges"].append({"from": "send_article", "handle": "next", "to": "w"})
    d["edges"].append({"from": "w", "handle": "next", "to": "assign_sales"})
    assert ("wait_range" not in codes(flow(d))) is ok


def test_unknown_team():
    d = buyer_seller()
    d["nodes"][1]["team"] = "VIP"
    assert "unknown_team" in codes(flow(d), "error")


def test_unreachable_is_warning():
    d = buyer_seller()
    d["nodes"].append({"id": "orphan", "type": "send_message", "text": "x"})
    issues = validate_flow(flow(d))
    assert [i.level for i in issues if i.code == "unreachable"] == ["warning"]


def test_http_url_is_warning():
    d = buyer_seller()
    d["nodes"].append({"id": "api", "type": "api_call", "url": "http://x.io"})
    d["edges"] += [{"from": "send_article", "handle": "next", "to": "api"},
                   {"from": "api", "handle": "success", "to": "assign_sales"},
                   {"from": "api", "handle": "failure", "to": "assign_sales"}]
    assert codes(flow(d)) == {"insecure_url"}


def test_https_url_ok():
    d = buyer_seller()
    d["nodes"].append({"id": "api", "type": "api_call", "url": "https://x.io"})
    d["edges"] += [{"from": "send_article", "handle": "next", "to": "api"},
                   {"from": "api", "handle": "success", "to": "assign_sales"},
                   {"from": "api", "handle": "failure", "to": "assign_sales"}]
    assert codes(flow(d)) == set()


def test_sensitive_question_warning():
    d = buyer_seller()
    d["nodes"][0]["text"] = "Please send your card number"
    assert codes(flow(d), "warning") == {"sensitive_data"}
    assert codes(flow(), "warning") == set()


def test_schema_error_raises():
    d = buyer_seller()
    d["nodes"][0]["type"] = "send_email"
    with pytest.raises(ValueError):
        flow(d)
