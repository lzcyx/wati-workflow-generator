# Automation Builder Copilot design

This take-home exercise focuses on delivering a single, usable workflow. The [Wati chatbot node reference](./wati-node-reference.md) defines the node vocabulary.

## 1. Scope

The user describes requirements over several turns. The agent builds or edits the flow, the validator checks it, and the agent explains the result in plain language. The user can then try the flow in the simulator.

| Current scope | V2 |
|---|---|
| The six required node types and two trigger types | AI, Template, Catalog, and other node types |
| Multi-turn conversation for creating flows, making incremental edits, and asking clarifying questions | Undo and rollback to previous versions. The current version history records each commit. |
| Deterministic validation | An additional LLM-based risk review |
| Agent-generated flows and a separate LLM call for explanations | Automatic test case generation and batch execution |
| Simulator with mock API responses | Live HTTP requests and publishing |
| In-memory storage in a single process | Persistence, authentication, and multiple users |
| Single-page frontend with chat, a flow graph, and a simulator | Manual node editing on the graph |

The exercise calls for at least one AI capability. This project provides **flow generation** and **plain-language explanations**.

## 2. Architecture

```text
frontend/  Vite + React + TypeScript + React Flow
backend/
  app/
    api/        FastAPI routes for handling requests and converting data
    domain/     Deterministic logic with no LLM dependency
      models.py     Flow, Node, Edge, and Trigger models (Pydantic)
      registry.py   Node purposes, fields, and output rules for prompts and validation
      edits.py      Atomic execution of edit batches
      validator.py  Graph validation
      engine.py     Simulation engine
      diff.py       Changes between flow versions
    copilot/    Agent prompts, tools, bounded tool loop, and explanations
    llm/        Thin wrapper around an OpenAI-compatible API
    store.py    In-memory storage
    config.py   pydantic-settings for reading .env
  tests/
```

Dependencies follow `api → copilot → domain` and `copilot → llm`. The **LLM proposes changes**, and **deterministic code validates and executes them**.

## 3. Data model

### 3.1 Flow

```json
{
  "id": "flow_x",
  "version": 3,
  "name": "Buyer / Seller routing",
  "trigger": { "type": "new_contact_message" },
  "start_node_id": "ask_role",
  "nodes": [],
  "edges": []
}
```

The `nodes` and `edges` arrays contain Node and Edge objects. For a keyword trigger, use `{ "type": "keyword", "keywords": ["hi"] }`.

### 3.2 Node types

| Type | Fields | Output handles | Can end a flow |
|---|---|---|---|
| `send_message` | `text` | `next` | Yes, when there is no outgoing edge |
| `ask_question` | `text`, `mode: buttons \| text`, `options[]` (1–3 in buttons mode only), `save_as` (variable name) | Buttons mode has one `option:<option>` output per option plus `fallback`. Text mode has `next`. | No |
| `condition` | `variable`, `operator: equals \| not_equals \| contains`, `value` | `true` / `false` | No |
| `api_call` | `method: GET \| POST`, `url`, `body?`, `save_as?` | `success` / `failure` | No |
| `assign_team` | `team` | `next` (optional) | Yes |
| `wait` | `duration_seconds` (1–86,400) | `next` | No |

- A flow ends at a `send_message` or `assign_team` node with no outgoing edge. Requiring outgoing edges on other node types lets the validator catch missing connections without an explicit End node.
- A Condition supports one rule. V2 adds AND/OR combinations.
- The option count and wait duration limits apply to this project.

### 3.3 Edges

```jsonc
{ "from": "ask_role", "handle": "option:Buyer", "to": "assign_sales" }
```

Each node output supports at most one edge. The `handle` must identify an output supported by the source node.

### 3.4 Variables

Variables come from `ask_question.save_as`, `api_call.save_as`, and the built-in contact fields `contact.name` and `contact.phone`. Refer to a variable with `{{variable_name}}` in a message or by name in a Condition's `variable` field.

The output taken determines whether the engine writes a variable.

| Node and output | Writes `save_as` | Value |
|---|---|---|
| `ask_question` in buttons mode, `option:<option>` | Yes | Original option text (see section 3.7) |
| `ask_question` in buttons mode, `fallback` | No | — |
| `ask_question` in text mode, `next` | Yes | User input with leading and trailing whitespace removed |
| `api_call`, `success` | Yes, if `save_as` is set | Mock response body |
| `api_call`, `failure` | No | — |

Every path from the start to a node that reads a variable must define the variable before the read. The validator reports an error if any of those paths leaves it undefined.

For example, suppose the buyer and seller branches merge, and only the seller branch asks for and saves `email`. A message reading `Sent to {{email}}` after the merge causes a validation error because the buyer path leaves `email` undefined.

The validator uses definite assignment analysis. At the start, only the built-in `contact.*` variables are defined. Each outgoing edge carries the variables defined when entering its source node plus any variable written by that output. The variables defined at a node's entry are the intersection of the sets from all its incoming edges. The validator repeats this calculation until the sets stop changing, which also handles cycles. Unreachable nodes are excluded because they have their own warning.

### 3.5 Mock workspace

```jsonc
{ "teams": ["Sales", "Support", "Billing"] }
```

The agent can use only teams listed in the workspace. It asks the user to clarify when a requested team is missing.

### 3.6 Sessions

```text
Session { id, messages[], versions[Flow], current_version }
```

- A new session has an empty `versions` list and `current_version` set to `null`.
- A turn appends a version only if the final diff contains changes. An empty diff keeps the current version.
- The version number increments on commit. A turn can commit at most once.

### 3.7 Option matching

The validator and execution engine share the `domain` function `normalize()`, which trims leading and trailing whitespace, collapses whitespace runs to one space, and applies case folding.

- **Duplicate options** cause a validation error when their normalized text is the same. For example, `Buyer` and ` buyer` are duplicates.
- **Matching order** starts with a comparison between normalized input and normalized option text. If there is no text match and the input is an integer from `1` through `n`, it selects the option at that position. Otherwise, it follows `fallback`.
- **Text matches take precedence.** With options `["2", "1"]`, the input `1` matches the text `"1"` in the second position.
- **The engine stores the original option text** in `save_as` for both text and position matches. If `Buyer` is the first option, both `1` and `buyer` save `"Buyer"` so a later `condition equals Buyer` behaves consistently.
- The output `handle` also uses the original text, for example `option:Buyer`.

## 4. Edit operations (`apply_edits`)

| Operation | Parameters | Behavior |
|---|---|---|
| `add_node` | `node` | The LLM supplies an ID such as `ask_role`. The system checks for duplicates. |
| `update_node` | `id`, `changes` | Send only the changed fields. The system merges them into the existing node and disallows changes to `type`. |
| `remove_node` | `id` | Removes the node and its connected edges. |
| `connect` | `from`, `handle`, `to` | Replaces any existing edge on the specified output. |
| `disconnect` | `from`, `handle` | |
| `set_trigger` | `trigger` | |
| `set_start` | `node_id` | |

The system applies each batch **atomically** to a copy of the draft. An invalid operation rejects the whole batch and returns an error that identifies the operation by its position.

### Diff

`diff.py` compares the previous and new flow versions directly. The same comparison works for incremental edits and `replace_flow`.

```json
{
  "kind": "added | removed | changed",
  "target": "node | edge | trigger | start",
  "id": "Node ID or from:handle for an edge",
  "before": "...",
  "after": "..."
}
```

Changed entries include both `before` and `after` values.

- Changing only the start produces one entry, for example `{ "kind": "changed", "target": "start", "before": "ask_role", "after": "greet" }`.
- Changing the trigger produces an entry with `target: "trigger"` and both the old and new values.

## 5. Validation rules

An **error** blocks saving a valid version and starting a simulation. A **warning** is advisory.

| Severity | Rule |
|---|---|
| error | The data fails Pydantic schema validation. |
| error | The start node is missing, an edge points to a missing node, or node IDs are duplicated. |
| error | A `handle` is invalid for its node, or multiple edges use the same output. |
| error | A required output has no connection. Required outputs include each option and `fallback`, `true` and `false`, `success` and `failure`, and `next` on `wait` and text-mode questions. |
| error | A cycle contains no `ask_question` node and can run indefinitely without waiting for input. |
| error | A question has fewer than one or more than three options, or has duplicates after normalization (see section 3.7). |
| error | The `wait` duration is outside the allowed range. |
| error | An `assign_team` node names a team outside the workspace. |
| error | A node reads a variable that at least one path to that node leaves undefined (see section 3.4). |
| warning | A node is unreachable from the starting node. |
| warning | An `api_call` URL uses a scheme other than HTTPS. |
| warning | A question appears to request sensitive information based on keywords such as password, card number, CVV, or OTP. |

Each issue has the structure `{ level, code, message, node_id? }`. The frontend uses `node_id` to highlight the relevant node in the graph.

## 6. Simulation engine

- The state is `{ current_node_id, variables, transcript[], trace[], status }`.
- Possible statuses are `waiting_input` (waiting for user input), `completed` (ended normally), `assigned` (handed off to a team), `error`, and `max_steps` (step limit exceeded).
- The engine advances from the current node until it reaches a node that requires input or a terminal state.
  - In buttons mode, `ask_question` matches input using section 3.7. On a match, it saves the original option text to `save_as` and takes that option's output. An unmatched input takes `fallback` without writing the variable.
  - In text mode, `ask_question` trims the input, saves it to the variable, and takes `next`.
  - `api_call` uses the caller's mock result, which defaults to success, and writes the mock response body on success when `save_as` is set. Failure leaves the variable unwritten.
  - The engine writes variables at the points defined in section 3.4. If a missing variable is read at runtime, the engine sets its status to `error`.
  - `wait` records the simulated delay in the trace and immediately continues.
  - `assign_team` records the team and sets the status to `assigned` if there is no following node.
  - In messages, `{{variable}}` is replaced with the variable's value.
- `SIM_MAX_STEPS` controls the step limit.
- The agent's `simulate` tool and the frontend simulator use the same execution engine.

## 7. Agent

### 7.1 Turn lifecycle

```text
User message
  → Load the current flow, recent messages, and workspace from the session
  → Run a bounded tool loop (up to AGENT_MAX_ITERATIONS steps, tool_choice=required)
       Apply edits to the draft and return validation results after each write
       End with finish or ask_user
  → Determine status using section 7.3 and commit a new version if eligible
       Commit at most once per turn
  → After a commit, make a best-effort LLM call for a plain-language explanation
       On failure or timeout, build a short explanation from the diff and finish.summary
       Preserve completed or partial and set explanation_source to "fallback"
  → Return the result
```

A pre-commit failure returns `failed` with the current flow intact, so retrying a failed turn cannot apply the same edit twice. A post-commit explanation failure preserves the committed version and status.

### 7.2 Tools

| Tool | Ends the turn | Purpose | Returns |
|---|---|---|---|
| `replace_flow(flow)` | No | Write the entire flow for initial generation or an explicit rebuild request. | Flow outline and validation results |
| `apply_edits(ops[])` | No | Apply incremental changes. This is the default for an existing flow. | Flow outline, validation results, and diff for this batch |
| `simulate(scenarios[])` | No | Check behavior against the user's intent. Each scenario contains user inputs and can specify mock API results. | Execution path and final status for each scenario |
| `ask_user(questions[])` | Yes | Ask up to three questions when key information is missing and cannot reasonably be inferred. | — |
| `finish(summary, assumptions[], unsupported[])` | Yes | Finalize the result. The system rejects drafts with errors, and the agent continues to fix them. | — |

- `replace_flow` creates an entire graph in one call. `apply_edits` limits changes on an existing graph to the requested fields and connections.
- Each write returns validation results in the same call, so the agent gets feedback immediately and validation runs on every write.
- `simulate` checks behavior that structural validation cannot detect. For example, a graph with the buyer and seller branches swapped can be structurally valid.
- Validation runs automatically after edits and at finalization, so the agent needs no separate validation tool.

### 7.3 Turn results

Evaluate the rows from top to bottom and stop at the first match. The diff compares the draft with the current valid version. A `finish` call is accepted only when the draft passes final validation.

| # | Condition | Status | Version | Returned `flow` and `version` |
|---|---|---|---|---|
| 1 | An LLM call in the agent loop fails after retries | `failed` | Unchanged | Previous version, or `null` if the session has no flow |
| 2 | The step limit is reached without an accepted terminal tool call, even if the draft is valid | `failed` | Unchanged | Previous version. The response includes the draft and its issues in `draft` and `issues`. |
| 3 | `ask_user` | `clarification` | Unchanged | Previous version, or **`null` when the first turn asks for clarification** |
| 4 | `finish` with a non-empty diff and empty `unsupported` | `completed` | +1 | New version |
| 5 | `finish` with a non-empty diff and non-empty `unsupported` | `partial` | +1 | New version |
| 6 | `finish` with an empty diff and non-empty `unsupported` | `declined` | Unchanged | Previous version, which may be `null` |
| 7 | `finish` with an empty diff and empty `unsupported`, such as an explanation request or an already satisfied requirement | `completed` | **Unchanged** | Previous version with `diff: []` |

Row 2 leaves even a valid draft uncommitted because the agent has yet to confirm it meets the user's intent or provide `unsupported`. Rejected `finish` calls count toward the step limit.

1. Record every requirement the builder cannot fulfill in `unsupported`.
2. Resolve validator errors before finalizing a draft. If the builder cannot fulfill a requirement, report it as unsupported.
3. Preserve the last valid version on unsuccessful turns.

### 7.4 System prompt

The system prompt contains the following sections in order.

1. **Role and principles.** Use the listed node types and known team names and links. Ask before choosing an alternative that changes the user's intent. Write Copilot responses and explanations in English. Use English for generated flow messages by default and follow explicit user requirements for flow content.
2. **Node catalog.** Generate it from `registry.py` to keep prompt and code in sync.
3. **Edit operation catalog.**
4. **Workspace information.**
5. **Worked example.** Show how the exercise's buyer and seller request becomes a flow.
6. **End-of-turn rules.** End each turn with `finish` or `ask_user`.

Dynamic context includes the current flow outline, recent messages, and the latest user message.

The provider receives tool parameters as plain objects. The backend uses Pydantic for validation and returns invalid arguments as tool errors so the loop can continue.

### 7.5 Failure handling and observability

- The SDK retries timed-out or rate-limited LLM calls up to `LLM_MAX_RETRIES` times.
  - If retries are exhausted in the agent loop before commit, the turn returns `failed` and keeps the current flow.
  - If retries are exhausted while generating the explanation after commit, the fallback explanation is used and the status and version are preserved (see section 7.1).
- Unknown tools and invalid arguments return errors to the agent and count toward the step limit.
- Each turn receives a `trace_id`. The trace records each tool name, an argument summary, validation results, and duration. The backend writes the trace to structured logs and includes it in the response for an expandable frontend view.

## 8. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions` | Create a session. |
| GET | `/api/sessions/{id}` | Get the session's conversation history and current flow. |
| POST | `/api/sessions/{id}/messages` | Send a message and run one agent turn. |
| POST | `/api/sessions/{id}/simulations` | Start a simulation of the current flow with optional API mocks. |
| POST | `/api/simulations/{sim_id}/input` | Send user input to a simulation. |

The message endpoint returns the following fields.

```json
{
  "status": "completed | partial | declined | clarification | failed",
  "reply": "A summary, a clarification question, or the reason the request was declined",
  "flow": {},
  "version": 3,
  "diff": [],
  "issues": [],
  "explanation": "Plain-language explanation",
  "explanation_source": "llm | fallback",
  "assumptions": [],
  "unsupported": [],
  "draft": null,
  "trace": []
}
```

The `flow` field holds the current valid flow. Both `flow` and `version` are `null` until a valid version exists. When a turn creates no new version, `diff` is empty and `explanation` is `null`. Section 4 defines the diff entries. The `issues` array holds validation findings, `trace` records agent steps, and `draft` can be populated on failed turns.

## 9. Frontend

The single-page frontend has three columns.

- The **left column** shows the Copilot conversation, including replies, clarifications, assumptions, and unsupported requests.
- The **center column** shows a React Flow graph laid out with dagre. Nodes with issues appear in red or yellow, and the current node is highlighted during simulation.
- The **right column** contains the Explanation and issues, Simulator, and JSON tabs.

## 10. Testing

| Area | Coverage |
|---|---|
| `domain` unit tests | Positive and negative cases for each validation rule, atomic edits, and engine behavior including fallback, API failure, step limits, and variable substitution. |
| Variable analysis | A merge fails validation if only one branch writes a required variable and passes if both write it. Reading a variable left unwritten by `fallback` or `failure` fails validation. Analysis converges on a flow with a cycle. |
| Option matching | `Buyer` and `buyer` count as duplicates. The inputs `1` and `buyer` both store `"Buyer"` for the first Buyer option. Numeric option text takes precedence over its position. |
| Diff | A turn that runs only `set_start` increments the version and returns exactly one `target: start` entry, with both values. |
| Agent loop | A scripted fake LLM covers every row in section 7.3, including first-turn clarification with a null flow, a valid draft at the step limit without `finish`, and `finish` with no edits. It also covers fixing a draft after validation rejects it. |
| Commit and explanation | The fake LLM succeeds in the agent loop and times out during explanation. The status is `completed`, the version increments exactly once, and `explanation_source` is `fallback`. |
| Demos | Run three natural-language scenarios manually with a live LLM. |

## 11. Demo scenarios

1. **Buyer and seller routing.** The agent asks for the help article link, builds and explains the flow, and simulates buyer, seller, and unexpected responses.
2. **Multi-turn edit.** Ask the agent to wait one hour after sending the seller's article and then ask whether it helped. Show the `apply_edits` result and the diff.
3. **Unknown team and unsupported action.** Ask the agent to route VIP users to the VIP team and send them an email. Show the clarification for the missing team and the email action listed in `unsupported`.
