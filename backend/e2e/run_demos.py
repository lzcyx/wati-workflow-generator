from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"    [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILURES.append(label)


def say(c: TestClient, sid: str, msg: str) -> dict[str, Any]:
    print(f"\n  USER: {msg}")
    t0 = time.perf_counter()
    r = c.post(f"/api/sessions/{sid}/messages", json={"content": msg})
    r.raise_for_status()
    d = r.json()
    print(f"  -> status={d['status']} version={d['version']} "
          f"({time.perf_counter() - t0:.1f}s, trace={d['trace_id']})")
    print("  steps: " + " | ".join(
        f"{t['tool']}{'' if t['ok'] else '(x)'}" for t in d["trace"]))
    print(f"  REPLY: {d['reply']}")
    if d["assumptions"]:
        print(f"  assumptions: {d['assumptions']}")
    if d["unsupported"]:
        print(f"  unsupported: {d['unsupported']}")
    if d["explanation"]:
        print(f"  EXPLANATION ({d['explanation_source']}):\n    "
              + d["explanation"].replace("\n", "\n    "))
    if d["diff"]:
        print("  diff: " + ", ".join(f"{x['kind']} {x['target']} {x.get('id') or ''}"
                                     for x in d["diff"]))
    errs = [i for i in d["issues"] if i["level"] == "error"]
    warns = [i for i in d["issues"] if i["level"] == "warning"]
    if errs or warns:
        print(f"  issues: {len(errs)} errors, {len(warns)} warnings: "
              + "; ".join(i["message"] for i in errs + warns))
    return d


def simulate(c: TestClient, sid: str, inputs: list[str],
             api_results: dict[str, str] | None = None) -> dict[str, Any]:
    sim = c.post(f"/api/sessions/{sid}/simulations",
                 json={"api_results": api_results or {}}).json()
    st = sim["state"]
    for text in inputs:
        if st["status"] != "waiting_input":
            break
        st = c.post(f"/api/simulations/{sim['id']}/input", json={"text": text}).json()["state"]
    print(f"  SIM {inputs} -> {st['status']} team={st['assigned_team']} "
          f"last={st['transcript'][-1]['text'][:80]!r}")
    return st


def node_types(flow: dict[str, Any]) -> list[str]:
    return [n["type"] for n in flow["nodes"]]


def demo1(c: TestClient) -> str | None:
    print("\n=== Demo 1: buyer / seller (clarify URL -> build -> simulate) ===")
    sid = c.post("/api/sessions").json()["id"]
    r = say(c, sid, "When a new contact messages us, ask if they're a buyer or seller, then "
                    "route buyers to sales and send sellers our help article.")
    check(r["status"] == "clarification", "asks for the missing help article URL")
    check(r["flow"] is None, "no flow committed on clarification")
    if r["status"] == "clarification":
        r = say(c, sid, "The article is https://help.example.com/sellers")
    check(r["status"] == "completed" and r["version"] == 1, "flow v1 committed")
    if not r["flow"]:
        return None
    f = r["flow"]
    check(f["trigger"]["type"] == "new_contact_message", "trigger = new contact message")
    check("ask_question" in node_types(f) and "assign_team" in node_types(f),
          "uses ask_question + assign_team")
    check(any("help.example.com/sellers" in json.dumps(n) for n in f["nodes"]),
          "help article URL present")
    check(not [i for i in r["issues"] if i["level"] == "error"], "no validation errors")
    buyer = simulate(c, sid, ["Buyer"])
    check(buyer["assigned_team"] == "Sales", "buyer -> Sales")
    seller = simulate(c, sid, ["Seller"])
    check(any("help.example.com/sellers" in t["text"] for t in seller["transcript"]),
          "seller receives the article")
    junk = simulate(c, sid, ["banana"])
    check(junk["status"] in ("waiting_input", "completed", "assigned"),
          "junk reply is handled (fallback)")
    return sid


def demo2(c: TestClient, sid: str | None) -> None:
    print("\n=== Demo 2: incremental edit (wait 1h then follow-up) ===")
    if sid is None:
        sid = c.post("/api/sessions").json()["id"]
        say(c, sid, "When a new contact messages us, ask if they're a buyer or seller, route "
                    "buyers to Sales and send sellers https://help.example.com/sellers")
    before = c.get(f"/api/sessions/{sid}").json()
    r = say(c, sid, "After sellers get the article, wait 1 hour and then ask whether it "
                    "helped them.")
    check(r["status"] == "completed" and r["version"] == (before["version"] or 0) + 1,
          "new version committed")
    if not r["flow"]:
        return
    check(any(n["type"] == "wait" and n["duration_seconds"] == 3600 for n in r["flow"]["nodes"]),
          "wait node of 3600s added")
    check(all(x["kind"] != "removed" for x in r["diff"] if x["target"] == "node"),
          "no existing node removed (minimal edit)")
    buyer = simulate(c, sid, ["Buyer"])
    check(buyer["assigned_team"] == "Sales", "buyer path unchanged")
    seller = simulate(c, sid, ["Seller"])
    check(any("3600" in t["text"] for t in seller["transcript"]), "seller path waits 1h")
    check(seller["status"] == "waiting_input", "seller is asked a follow-up question")


def demo3(c: TestClient) -> None:
    print("\n=== Demo 3: unknown team + unsupported email ===")
    sid = c.post("/api/sessions").json()["id"]
    r = say(c, sid, "When someone messages with keyword VIP, assign them to the VIP team and "
                    "also send them an email.")
    check(r["status"] in ("clarification", "partial", "declined"),
          "does not silently build an invalid VIP-team flow")
    if r["flow"]:
        teams = [n.get("team") for n in r["flow"]["nodes"] if n["type"] == "assign_team"]
        check("VIP" not in teams, "never uses the non-existent VIP team")
    if r["status"] == "clarification":
        r = say(c, sid, "OK, use the Support team instead.")
        check(r["status"] == "partial", "builds what it can -> partial")
        check(any("mail" in u.lower() for u in r["unsupported"]),
              "email marked unsupported")
        if r["flow"]:
            check(r["flow"]["trigger"].get("keywords") and
                  any(k.lower() == "vip" for k in r["flow"]["trigger"]["keywords"]),
                  "keyword trigger 'VIP'")
            sim = simulate(c, sid, [])
            check(sim["assigned_team"] == "Support", "assigned to Support")


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import get_settings
    from app.main import create_app

    settings = get_settings()
    print(f"model={settings.llm_model} base_url={settings.llm_base_url} "
          f"max_iter={settings.agent_max_iterations} logs={settings.log_path}")
    c = TestClient(create_app(settings))
    which = set(sys.argv[1:]) or {"1", "2", "3"}
    sid = None
    if "1" in which:
        sid = demo1(c)
    if "2" in which:
        demo2(c, sid)
    if "3" in which:
        demo3(c)
    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else f'{len(FAILURES)} FAILED:'}")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
