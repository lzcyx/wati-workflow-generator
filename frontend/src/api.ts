
export type NodeType =
  | "send_message"
  | "ask_question"
  | "condition"
  | "api_call"
  | "assign_team"
  | "wait";

export interface FlowNode {
  id: string;
  type: NodeType;
  [field: string]: unknown;
}

export interface FlowEdge {
  from: string;
  handle: string;
  to: string;
}

export interface Flow {
  id: string;
  version: number;
  name: string;
  trigger: { type: "new_contact_message" } | { type: "keyword"; keywords: string[] };
  start_node_id: string | null;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export interface Issue {
  level: "error" | "warning";
  code: string;
  message: string;
  node_id?: string | null;
}

export interface Change {
  kind: "added" | "removed" | "changed";
  target: "node" | "edge" | "trigger" | "start" | "name";
  id?: string | null;
  before?: unknown;
  after?: unknown;
}

export interface TraceStep {
  step: number;
  tool: string | null;
  args_summary?: string | null;
  ok: boolean;
  result_summary?: string | null;
  errors: number;
  warnings: number;
  duration_ms: number;
}

export type TurnStatus = "completed" | "partial" | "declined" | "clarification" | "failed";

export interface TurnResult {
  status: TurnStatus;
  reply: string;
  flow: Flow | null;
  version: number | null;
  diff: Change[];
  issues: Issue[];
  explanation: string | null;
  explanation_source: "llm" | "fallback" | null;
  assumptions: string[];
  unsupported: string[];
  questions: string[];
  suggestions: string[];
  draft: Flow | null;
  trace_id: string;
  trace: TraceStep[];
}

type TurnUpdate =
  | { type: "step"; step: TraceStep }
  | { type: "reply"; turn: TurnResult }
  | { type: "explanation"; delta: string };

type TurnStreamEvent =
  | TurnUpdate
  | { type: "result"; turn: TurnResult }
  | { type: "error"; message: string };

export interface SessionOut {
  id: string;
  messages: { role: "user" | "assistant"; content: string }[];
  flow: Flow | null;
  version: number | null;
  versions: number[];
  issues: Issue[];
  workspace: { teams: string[] };
}

export interface SimState {
  current_node_id: string | null;
  variables: Record<string, unknown>;
  transcript: { role: "bot" | "user" | "system"; text: string; node_id?: string | null; options?: string[] | null }[];
  trace: { node_id: string; type: string; detail: string; handle?: string | null }[];
  status: "running" | "waiting_input" | "completed" | "assigned" | "error" | "max_steps";
  error: string | null;
  assigned_team: string | null;
  steps: number;
}

export interface SimulationOut {
  id: string;
  flow_version: number;
  state: SimState;
}

async function request(
  method: string, path: string, body?: unknown, accept = "application/json",
): Promise<Response> {
  const headers = new Headers({ Accept: accept });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const r = await fetch(`/api${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail: unknown = r.statusText;
    try {
      detail = (await r.json()).detail;
    } catch {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  return (await request(method, path, body)).json() as Promise<T>;
}

async function streamMessage(
  sid: string,
  content: string,
  onUpdate: (event: TurnUpdate) => void,
): Promise<TurnResult> {
  const response = await request("POST", `/sessions/${sid}/messages`, { content }, "application/x-ndjson");
  if (response.headers.get("content-type")?.startsWith("application/json")) {
    return response.json() as Promise<TurnResult>;
  }
  if (!response.body) throw new Error("The server returned no reply stream.");

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;
      for (let end = buffer.indexOf("\n"); end >= 0; end = buffer.indexOf("\n")) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 1);
        if (!line) continue;
        const event: TurnStreamEvent = JSON.parse(line);
        if (event.type === "result") return event.turn;
        if (event.type === "error") throw new Error(event.message);
        onUpdate(event);
      }
    }
    throw new Error("The reply stream ended before the result arrived.");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export const api = {
  createSession: () => req<SessionOut>("POST", "/sessions"),
  streamMessage,
  startSimulation: (sid: string, api_results: Record<string, "success" | "failure">) =>
    req<SimulationOut>("POST", `/sessions/${sid}/simulations`, { api_results }),
  simInput: (simId: string, text: string) =>
    req<SimulationOut>("POST", `/simulations/${simId}/input`, { text }),
};
