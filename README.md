# Wati Automation Builder Copilot

Wati Automation Builder Copilot uses a large language model (LLM) to build, edit, and explain Wati chatbot workflows from plain-language requests. The app validates each workflow and lets you inspect the flow graph and simulate conversations.

https://github.com/user-attachments/assets/ed505011-811c-4200-bb2f-460dda07d870

## Start

From the project root, copy the environment template.

```bash
cp .env.example .env
```

Open `.env` and set `LLM_API_KEY` and `LLM_MODEL`. If you use another OpenAI-compatible provider, set `LLM_BASE_URL` to its endpoint.

With Docker running, start both services from the project root.

```bash
docker compose up --build -d --wait
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080).
