# Wati Workflow Generator — MVP Write-up

**Problem and MVP scope.** Building a workflow by hand takes time, especially when it has several branches. The MVP lets users describe a workflow in plain language, refine it through chat, and debug it in a browser simulator by trying different inputs and inspecting the execution trace.

**Architecture and key decisions.** A React frontend presents the chat, workflow graph, and simulator. A FastAPI backend runs an LLM agent that uses tools to edit a draft. A deterministic validator checks the workflow before it can be saved as a new version. The simulator executes the validated graph independently of the LLM, so the same inputs produce the same result.

**Use of AI.** I used Claude Code with Opus 5.5 to design the architecture, then Codex with GPT‑6.2 Astra to review the design and implement it.

**Manual review.** I reviewed the core agent flow, from interpreting the user's request and calling tools through validating the draft and deciding whether to commit it.

**Trade-offs and V2.** In-memory sessions kept the MVP simple, but work in progress can be lost if the backend restarts. In V2, I would persist checkpoints so an interrupted agent run can resume from its last completed step without applying the same change twice.
