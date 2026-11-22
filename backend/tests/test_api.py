from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.client import LLMError
from app.main import create_app
from tests.helpers import ScriptedLLM, buyer_seller, call, text


@pytest.fixture
def client_llm(tmp_path):
    llm = ScriptedLLM([])
    settings = Settings(_env_file=None, llm_api_key="x", llm_model="fake",
                        log_dir=str(tmp_path))
    app = create_app(settings, llm_factory=lambda: llm)
    return TestClient(app), llm


def test_full_http_roundtrip(client_llm):
    c, llm = client_llm
    sid = c.post("/api/sessions").json()["id"]
    assert c.get(f"/api/sessions/{sid}").json()["flow"] is None

    llm.script += [call("ask_user", questions=["Help article URL?"])]
    r = c.post(f"/api/sessions/{sid}/messages", json={"content": "buyer/seller"}).json()
    assert r["status"] == "clarification" and r["flow"] is None and r["version"] is None

    assert c.post(f"/api/sessions/{sid}/simulations", json={}).status_code == 409

    llm.script += [call("replace_flow", flow=buyer_seller()),
                   call("finish", summary="Done", assumptions=["retry on junk"]),
                   text("Plain explanation")]
    r = c.post(f"/api/sessions/{sid}/messages",
               json={"content": "https://x.io/help"}).json()
    assert r["status"] == "completed" and r["version"] == 1
    assert r["explanation"] == "Plain explanation" and r["explanation_source"] == "llm"
    assert r["diff"] and r["trace"] and r["trace_id"].startswith("t_")
    assert r["flow"]["edges"][0]["from"] == "ask_role"

    sess = c.get(f"/api/sessions/{sid}").json()
    assert sess["version"] == 1 and len(sess["messages"]) == 4

    sim = c.post(f"/api/sessions/{sid}/simulations", json={}).json()
    assert sim["state"]["status"] == "waiting_input"
    sim = c.post(f"/api/simulations/{sim['id']}/input", json={"text": "2"}).json()
    assert sim["state"]["status"] == "completed"
    assert sim["state"]["variables"]["role"] == "Seller"
    assert c.post(f"/api/simulations/{sim['id']}/input", json={"text": "x"}).status_code == 409


def test_404s(client_llm):
    c, _ = client_llm
    assert c.get("/api/sessions/nope").status_code == 404
    assert c.post("/api/simulations/nope/input", json={"text": "x"}).status_code == 404
    assert c.post("/api/sessions/nope/messages", json={"content": "x"},
                  headers={"Accept": "application/x-ndjson"}).status_code == 404


@pytest.mark.parametrize("stream_fails", [False, True])
def test_stream_roundtrip(client_llm, monkeypatch, stream_fails):
    c, llm = client_llm
    sid = c.post("/api/sessions").json()["id"]
    llm.script += [call("replace_flow", flow=buyer_seller()), call("finish", summary="Saved")]
    if stream_fails:
        llm.script.append(text("Recovered explanation"))

    def explain(_messages, on_text):
        on_text("Plain ")
        if stream_fails:
            raise LLMError("stream interrupted")
        on_text("explanation")
        return text("Plain explanation")

    monkeypatch.setattr(llm, "stream_text", explain, raising=False)
    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"content": "Build the flow"},
                  headers={"Accept": "application/x-ndjson"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/x-ndjson"
        events = [json.loads(line) for line in response.iter_lines() if line]

    assert [event["type"] for event in events[:3]] == ["step", "step", "reply"]
    assert events[2]["turn"]["version"] == 1 and events[2]["turn"]["explanation"] is None
    assert all(event["type"] == "explanation" for event in events[3:-1])
    assert [event["delta"] for event in events[3:-1]] == (
        ["Plain "] if stream_fails else ["Plain ", "explanation"]
    )
    assert events[-1]["type"] == "result"
    turn = events[-1]["turn"]
    assert turn["reply"] == "Saved" and turn["explanation_source"] == "llm"
    assert turn["explanation"] == ("Recovered explanation" if stream_fails else "Plain explanation")
    session = c.get(f"/api/sessions/{sid}").json()
    assert session["versions"] == [1] and len(session["messages"]) == 2


def test_stream_can_finish_without_an_explanation(client_llm):
    c, llm = client_llm
    sid = c.post("/api/sessions").json()["id"]
    llm.script.append(call("ask_user", questions=["Which team?"]))

    with c.stream("POST", f"/api/sessions/{sid}/messages", json={"content": "Transfer the chat"},
                  headers={"Accept": "application/x-ndjson"}) as response:
        events = [json.loads(line) for line in response.iter_lines() if line]

    assert [event["type"] for event in events] == ["step", "result"]
    assert events[-1]["turn"]["status"] == "clarification"
    assert events[-1]["turn"]["questions"] == ["Which team?"]
