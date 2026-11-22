import { useMemo } from "react";
import dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Flow, FlowNode, Issue } from "./api";

const W = 240;
const H = 86;

const LABELS: Record<string, string> = {
  send_message: "Send Message",
  ask_question: "Ask Question",
  condition: "Condition",
  api_call: "API Call",
  assign_team: "Assign to Team",
  wait: "Wait",
  trigger: "Trigger",
};

function summarize(n: FlowNode): string {
  switch (n.type) {
    case "send_message":
      return String(n.text);
    case "ask_question":
      return `${n.text}${n.mode === "buttons" ? `  [${(n.options as string[]).join(" / ")}]` : " (free text)"}${n.save_as ? ` → {{${n.save_as}}}` : ""}`;
    case "condition":
      return `${n.variable} ${n.operator} "${n.value}"`;
    case "api_call":
      return `${n.method} ${n.url}`;
    case "assign_team":
      return `Team: ${n.team}`;
    case "wait":
      return formatDuration(Number(n.duration_seconds));
  }
  return "";
}

function formatDuration(s: number): string {
  if (s % 3600 === 0) return `${s / 3600} hour(s)`;
  if (s % 60 === 0) return `${s / 60} minute(s)`;
  return `${s} second(s)`;
}

type CardData = { kind: string; title: string; body: string; level?: "error" | "warning"; active: boolean; isStart: boolean };

function Card({ data }: NodeProps<Node<CardData>>) {
  const cls = ["card", `card-${data.kind}`, data.level ? `card-${data.level}` : "", data.active ? "card-active" : ""].join(" ");
  return (
    <div className={cls}>
      {data.kind !== "trigger" && <Handle type="target" position={Position.Top} />}
      <div className="card-title">
        {LABELS[data.kind] ?? data.kind}
        <span className="card-id">{data.title}</span>
      </div>
      <div className="card-body">{data.body}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { card: Card };

export function FlowGraph({ flow, issues, activeNodeId }: { flow: Flow | null; issues: Issue[]; activeNodeId: string | null }) {
  const { nodes, edges } = useMemo(() => build(flow, issues, activeNodeId), [flow, issues, activeNodeId]);
  if (!flow) {
    return <div className="empty">No flow yet — describe your automation in the chat.</div>;
  }
  return (
    <ReactFlow key={flow.version} fitViewOptions={{ padding: 0.15, maxZoom: 1 }} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView nodesDraggable={false} nodesConnectable={false}>
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

function build(flow: Flow | null, issues: Issue[], activeNodeId: string | null): { nodes: Node<CardData>[]; edges: Edge[] } {
  if (!flow) return { nodes: [], edges: [] };
  const levelOf = new Map<string, "error" | "warning">();
  for (const i of issues) {
    if (!i.node_id) continue;
    if (i.level === "error" || !levelOf.has(i.node_id)) levelOf.set(i.node_id, i.level);
  }

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 50, ranksep: 70 });
  g.setDefaultEdgeLabel(() => ({}));

  const trig = flow.trigger.type === "keyword" ? `Keyword: ${flow.trigger.keywords.join(", ")}` : "New contact sends a message";
  const rfNodes: Node<CardData>[] = [
    { id: "__trigger", type: "card", position: { x: 0, y: 0 }, data: { kind: "trigger", title: "", body: trig, active: false, isStart: false } },
  ];
  g.setNode("__trigger", { width: W, height: H });
  for (const n of flow.nodes) {
    g.setNode(n.id, { width: W, height: H });
    rfNodes.push({
      id: n.id,
      type: "card",
      position: { x: 0, y: 0 },
      data: { kind: n.type, title: n.id, body: summarize(n), level: levelOf.get(n.id), active: n.id === activeNodeId, isStart: n.id === flow.start_node_id },
    });
  }

  const ids = new Set(flow.nodes.map((n) => n.id));
  const rfEdges: Edge[] = [];
  if (flow.start_node_id && ids.has(flow.start_node_id)) {
    g.setEdge("__trigger", flow.start_node_id);
    rfEdges.push({ id: "__start", source: "__trigger", target: flow.start_node_id, animated: true, markerEnd: { type: MarkerType.ArrowClosed } });
  }
  for (const e of flow.edges) {
    if (!ids.has(e.from) || !ids.has(e.to)) continue;
    g.setEdge(e.from, e.to);
    const label = e.handle === "next" ? undefined : e.handle.replace(/^option:/, "");
    rfEdges.push({
      id: `${e.from}:${e.handle}`,
      source: e.from,
      target: e.to,
      label,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      className: e.handle === "fallback" || e.handle === "failure" || e.handle === "false" ? "edge-alt" : undefined,
    });
  }

  dagre.layout(g);
  for (const n of rfNodes) {
    const p = g.node(n.id);
    n.position = { x: p.x - W / 2, y: p.y - H / 2 };
  }
  return { nodes: rfNodes, edges: rfEdges };
}
