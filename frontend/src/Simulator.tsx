import { useEffect, useRef, useState } from "react";
import { api, type Flow, type SimulationOut } from "./api";

export function Simulator({ sessionId, flow, blocked, onActiveNode }: {
  sessionId: string;
  flow: Flow | null;
  blocked: boolean;
  onActiveNode: (id: string | null) => void;
}) {
  const [sim, setSim] = useState<SimulationOut | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [apiResults, setApiResults] = useState<Record<string, "success" | "failure">>({});
  const endRef = useRef<HTMLDivElement>(null);

  const apiNodes = flow?.nodes.filter((n) => n.type === "api_call") ?? [];

  useEffect(() => {
    setSim(null);
    onActiveNode(null);
  }, [flow?.version]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [sim]);

  const update = (s: SimulationOut) => {
    setSim(s);
    onActiveNode(s.state.status === "waiting_input" ? s.state.current_node_id : null);
  };

  const start = async () => {
    setError(null);
    try {
      update(await api.startSimulation(sessionId, apiResults));
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const send = async (text: string) => {
    if (!sim) return;
    setInput("");
    try {
      update(await api.simInput(sim.id, text));
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  if (!flow) return <div className="muted">Generate a flow first.</div>;
  const st = sim?.state;
  const last = st?.transcript.filter((t) => t.role === "bot").at(-1);

  return (
    <div className="sim">
      {apiNodes.length > 0 && (
        <div className="sim-mocks">
          <b>API mock results</b>
          {apiNodes.map((n) => (
            <label key={n.id}>
              {n.id}:{" "}
              <select value={apiResults[n.id] ?? "success"} onChange={(e) => setApiResults({ ...apiResults, [n.id]: e.target.value as "success" | "failure" })}>
                <option value="success">success</option>
                <option value="failure">failure</option>
              </select>
            </label>
          ))}
        </div>
      )}
      <button onClick={start} disabled={blocked}>
        {sim ? "Restart simulation" : "Start simulation"} (v{flow.version})
      </button>
      {blocked && <div className="warn">The flow has validation errors; fix them before simulating.</div>}
      {error && <div className="error-text">{error}</div>}
      {st && (
        <>
          <div className="phone">
            {st.transcript.map((t, i) => (
              <div key={i} className={`bubble bubble-${t.role}`}>{t.text}</div>
            ))}
            <div ref={endRef} />
          </div>
          <div className={`sim-status status-${st.status}`}>
            Status: <b>{st.status}</b>
            {st.assigned_team && <> · assigned to <b>{st.assigned_team}</b></>}
            {st.error && <> · {st.error}</>}
          </div>
          {st.status === "waiting_input" && (
            <>
              {last?.options && (
                <div className="quick">
                  {last.options.map((o) => (
                    <button key={o} onClick={() => send(o)}>{o}</button>
                  ))}
                </div>
              )}
              <form className="row" onSubmit={(e) => { e.preventDefault(); send(input); }}>
                <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Type a reply as the contact…" />
                <button type="submit">Send</button>
              </form>
            </>
          )}
          <details>
            <summary>Execution trace & variables</summary>
            <ol className="trace">
              {st.trace.map((t, i) => (
                <li key={i}><code>{t.node_id}</code> {t.detail}</li>
              ))}
            </ol>
            <pre>{JSON.stringify(st.variables, null, 2)}</pre>
          </details>
        </>
      )}
    </div>
  );
}
