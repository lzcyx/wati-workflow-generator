import { useEffect, useRef, useState } from "react";
import { api, type Change, type Flow, type Issue, type TurnResult } from "./api";
import { FlowGraph } from "./FlowGraph";
import { Simulator } from "./Simulator";

type ChatItem =
  | { role: "user"; text: string }
  | { role: "assistant"; turn: TurnResult }
  | { role: "error"; text: string };

const EXAMPLES = [
  "When a new contact messages us, ask if they're a buyer or seller. Route buyers to the Sales team and send sellers our help article https://help.example.com/sellers. If the reply is neither, ask again.",
  'When someone sends the keyword "refund", ask whether it\'s about a damaged item or a late delivery. Damaged item: assign to the Support team. Late delivery: apologise, wait 1 hour, then assign to the Billing team. Any other reply: ask again.',
  "When someone messages with keyword VIP, assign them to the VIP team and also send them an email.",
];

const TOOL_PROGRESS: Record<string, string> = {
  replace_flow: "Draft created. Reviewing the workflow…",
  apply_edits: "Changes applied. Reviewing the workflow…",
  simulate: "Simulation complete. Preparing the reply…",
  finish: "Preparing the reply…",
  ask_user: "Preparing a question…",
};

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [teams, setTeams] = useState<string[]>([]);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [lastTurn, setLastTurn] = useState<TurnResult | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [preview, setPreview] = useState("");
  const [creatingSession, setCreatingSession] = useState(false);
  const [input, setInput] = useState("");
  const [tab, setTab] = useState<"explain" | "sim" | "json">("explain");
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const busy = progress !== null;

  const newSession = async () => {
    setCreatingSession(true);
    try {
      const s = await api.createSession();
      setSessionId(s.id);
      setTeams(s.workspace.teams);
      setItems([]);
      setFlow(null);
      setIssues([]);
      setLastTurn(null);
    } catch (e) {
      setItems((x) => [...x, { role: "error", text: `Could not start a session: ${(e as Error).message}` }]);
    } finally {
      setCreatingSession(false);
    }
  };

  useEffect(() => {
    void newSession();
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "instant" });
  }, [items, progress, preview]);

  const showTurn = (turn: TurnResult) => {
    setFlow(turn.flow);
    setIssues(turn.issues);
    setLastTurn(turn);
    if (turn.diff.length) setTab("explain");
  };

  const send = async (text: string) => {
    if (!sessionId || !text.trim() || busy || creatingSession) return;
    setInput("");
    setItems((x) => [...x, { role: "user", text }]);
    setProgress("Thinking…");
    let replyReceived = false;
    try {
      const result = await api.streamMessage(sessionId, text, (event) => {
        if (event.type === "preview") {
          setPreview(event.text);
        } else if (event.type === "step") {
          if (!event.step.ok) setPreview("");
          setProgress(event.step.ok
            ? TOOL_PROGRESS[event.step.tool ?? ""] ?? "Working on your request…"
            : "Reviewing issues…");
        } else if (event.type === "reply") {
          replyReceived = true;
          setPreview("");
          setItems((x) => [...x, { role: "assistant", turn: event.turn }]);
          showTurn(event.turn);
          setProgress("Writing the explanation…");
        } else {
          setLastTurn((turn) => turn && {
            ...turn,
            explanation: (turn.explanation ?? "") + event.delta,
            explanation_source: "llm",
          });
        }
      });
      setItems((x) => [...(replyReceived ? x.slice(0, -1) : x), { role: "assistant", turn: result }]);
      if (replyReceived) setLastTurn(result);
      else showTurn(result);
    } catch (e) {
      if (replyReceived) {
        setLastTurn((turn) => turn && { ...turn, explanation: null, explanation_source: null });
      }
      setItems((x) => [...x, { role: "error", text: (e as Error).message }]);
    } finally {
      setProgress(null);
      setPreview("");
    }
  };

  const hasErrors = issues.some((i) => i.level === "error");
  const last = items.at(-1);

  return (
    <div className="app">
      <header>
        <b>Wati Automation Builder Copilot</b>
        <span className="muted"> · teams: {teams.join(", ")}</span>
        <span className="spacer" />
        {flow && <span className="pill">{flow.name} · v{flow.version}</span>}
        <button onClick={newSession} disabled={busy || creatingSession}>New session</button>
      </header>
      <main>
        <section className="chat">
          <div className="messages">
            {items.length === 0 && (
              <div className="examples">
                <div className="muted">Try one of these:</div>
                {EXAMPLES.map((e) => (
                  <button key={e} className="example" onClick={() => send(e)}>{e}</button>
                ))}
              </div>
            )}
            {items.map((it, i) => <ChatBubble key={i} item={it} />)}
            {!busy && last?.role === "assistant" && last.turn.suggestions.length > 0 && (
              <div className="suggestions">
                {last.turn.suggestions.map((sug) => (
                  <button key={sug} className="suggestion" onClick={() => send(sug)}>{sug}</button>
                ))}
              </div>
            )}
            {preview ? <div className="msg msg-assistant pre">{preview}</div> :
              progress && <div className="msg msg-assistant muted" role="status">{progress}</div>}
            <div ref={endRef} />
          </div>
          <form className="row" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder="Describe the automation you want…"
              rows={3}
            />
            <button type="submit" disabled={busy || creatingSession || !sessionId || !input.trim()}>Send</button>
          </form>
        </section>
        <section className="graph">
          <div className="graph-inner">
            <FlowGraph flow={flow} issues={issues} activeNodeId={activeNode} />
          </div>
        </section>
        <section className="side">
          <nav className="tabs">
            <button className={tab === "explain" ? "on" : ""} onClick={() => setTab("explain")}>
              Explanation & issues{issues.length ? ` (${issues.length})` : ""}
            </button>
            <button className={tab === "sim" ? "on" : ""} onClick={() => setTab("sim")}>Simulator</button>
            <button className={tab === "json" ? "on" : ""} onClick={() => setTab("json")}>JSON</button>
          </nav>
          <div className="tab-body">
            {tab === "explain" && <ExplainTab turn={lastTurn} issues={issues} />}
            {tab === "sim" && sessionId && <Simulator sessionId={sessionId} flow={flow} blocked={hasErrors} onActiveNode={setActiveNode} />}
            {tab === "json" && <pre className="json">{flow ? JSON.stringify(flow, null, 2) : "null"}</pre>}
          </div>
        </section>
      </main>
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  completed: "Done",
  partial: "Partially done",
  declined: "Not possible",
  clarification: "Needs your input",
  failed: "Failed",
};

function ChatBubble({ item }: { item: ChatItem }) {
  if (item.role === "user") return <div className="msg msg-user">{item.text}</div>;
  if (item.role === "error") return <div className="msg msg-error">{item.text}</div>;
  const t = item.turn;
  return (
    <div className={`msg msg-assistant status-${t.status}`}>
      <div className="status-tag">
        {STATUS_LABEL[t.status]}
        {t.version !== null && t.diff.length > 0 && <> · saved v{t.version}</>}
      </div>
      {t.questions.length > 0 ? (
        <ul>{t.questions.map((q) => <li key={q}>{q}</li>)}</ul>
      ) : (
        <div className="pre">{t.reply}</div>
      )}
      {t.assumptions.length > 0 && (
        <div className="sub"><b>Assumptions</b><ul>{t.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></div>
      )}
      {t.unsupported.length > 0 && (
        <div className="sub warn"><b>Not supported</b><ul>{t.unsupported.map((a) => <li key={a}>{a}</li>)}</ul></div>
      )}
      <details className="sub">
        <summary>Agent trace ({t.trace.length} steps · {t.trace_id})</summary>
        <ol className="trace">
          {t.trace.map((s, i) => (
            <li key={i} className={s.ok ? "" : "error-text"}>
              <code>{s.tool ?? "(no tool)"}</code> {s.duration_ms}ms
              {s.errors > 0 && <> · {s.errors} errors</>} · {s.result_summary}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

function describeChange(c: Change): string {
  const what = c.target === "edge" ? `connection ${c.id}` : c.target === "node" ? `step ${c.id}` : c.target;
  if (c.kind === "changed" && (c.target === "start" || c.target === "edge" || c.target === "name")) {
    return `${what}: ${String(c.before)} → ${String(c.after)}`;
  }
  return `${c.kind} ${what}`;
}

function ExplainTab({ turn, issues }: { turn: TurnResult | null; issues: Issue[] }) {
  return (
    <div>
      {turn?.explanation ? (
        <>
          <h4>In plain words {turn.explanation_source === "fallback" && <span className="muted">(fallback)</span>}</h4>
          <div className="pre">{turn.explanation}</div>
        </>
      ) : (
        <div className="muted">The explanation appears here after the copilot saves a new version.</div>
      )}
      {turn && turn.diff.length > 0 && (
        <>
          <h4>Changes in v{turn.version}</h4>
          <ul className="diff">
            {turn.diff.map((c, i) => <li key={i} className={`diff-${c.kind}`}>{describeChange(c)}</li>)}
          </ul>
        </>
      )}
      <h4>Validation</h4>
      {issues.length === 0 ? (
        <div className="ok-text">No issues found.</div>
      ) : (
        <ul className="issues">
          {issues.map((i, k) => (
            <li key={k} className={`issue-${i.level}`}>
              <b>{i.level}</b> {i.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
