# La Bella Vista — Restaurant Reservation Agent

A Dockerized, domain-specific AI agent for a restaurant's customer support and reservation workflow. Built for the *Dockerized Domain-Specific AI Agent* course project.

The agent answers grounded questions about the restaurant (menu, hours, policies), and lets customers book, modify, and cancel table reservations through natural conversation — with explicit workflow state, confirmation gates on every state-changing action, and a deterministic fallback when a request is ambiguous or unsupported.

> **Course:** Mini Projet — Dr. Mohamad Aoude, Faculty of Engineering
> **Video presentation link:** [ https://youtu.be/cQ-u_QT4oAU ]

## Team

| Student | Role | Responsibilities |
|---|---|---|
| **Mariam Al Hajjar - 6697** | Tools Engineer | Typed tools, validation, domain data, database schema |
| **Fatima El Tahech - 6637** | Agent Engineer | Workflow, routing, prompts, stopping rules, fallback logic |
| **Sana Damaj - 6606** | Platform & Interface | Memory/state, web interface, Docker packaging, trace logging, testing |


## Features

- **Grounded Q&A** — answers about the menu, hours, policies, and FAQs, sourced only from structured domain data (`data/*.json`), never invented.
- **Table reservations** — book, modify, or cancel a reservation across a multi-turn conversation.
- **Four required tools**, each with a documented input/output schema and explicit error codes:
  - *Information tool* (`tools/info_tool.py`) — looks up menu items, FAQs, and policies.
  - *Analysis tool* (`tools/availability_tool.py`) — checks table availability against party size, date, and time.
  - *Action tool* (`tools/booking_tool.py`) — creates, modifies, or cancels a reservation; state-changing, so it always requires explicit user confirmation before writing to the database.
  - *Reporting tool* (`tools/reporting_tool.py`) — generates a structured booking confirmation/summary.
- **Working memory** — every turn explicitly tracks current intent, collected fields, missing fields, pending confirmation, the latest tool result, and workflow state (`agent/state.py`).
- **Short-term memory** — full conversation history and the customer's name persist for the session (LangGraph `MemorySaver`, keyed per browser tab).
- **Long-term memory (bonus)** — returning customers' visit history (last party size, table location) persists across separate sessions in `bookings.db` and is used to personalize follow-up questions.
- **Deterministic safety rails** — a keyword-based router (`agent/deterministic_router.py`) is the only path used to interpret yes/no confirmations for sensitive actions, rather than trusting the LLM to parse free-form replies to something as consequential as "cancel my reservation."
- **Safe fallback** — unsupported or ambiguous requests get an honest "I can't help with that here" response with a simulated human-handoff offer, never an invented answer.

## Architecture

```
Gradio chat UI (app.py)
        │
Orchestration layer — LangGraph StateGraph (agent/graph.py, agent/nodes.py)
  interpret → [ask_missing_info | request_confirmation | execute_tools] → generate_response
        │
LLM reasoning core — Claude (agent/llm_client.py): intent classification + reply generation
        │
Tool layer (tools/) — info, availability, booking, reporting
        │
Memory layer — LangGraph MemorySaver (short-term/working) + bookings.db (long-term)
        │
Data layer — data/*.json (menu, tables, FAQs) + SQLite (bookings, preferences)
        │
Container layer — Docker (non-root user, health check, named volumes)
```

No RAG pipeline, vector database, or embeddings are used — domain data is small and structured, so it's stored in JSON/SQLite and accessed through deterministic tool functions.

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| Orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) | Explicit state graph — matches the workflow-states-and-transitions requirement directly |
| LLM | Anthropic Claude (`claude-haiku-4-5`) | Intent classification + natural-language reply generation |
| Web interface | [Gradio](https://gradio.app) (`5.50.0`, pinned) | Fast to stand up a chat UI inside Docker |
| Data | JSON (menu, FAQs) + SQLite (bookings, preferences) | Small, structured domain data — explicitly no RAG/vector DB per assignment scope |
| Container | Docker + Docker Compose | Non-root user, health check, named volumes, one-command startup |
| Testing | pytest, custom evaluation runner | Unit tests (node/tool logic) + 20-case conversational evaluation suite |

## Project structure

```
restaurant-agent/
├── app.py                     # Gradio web interface (entrypoint)
├── agent/
│   ├── graph.py                # LangGraph StateGraph wiring
│   ├── nodes.py                # All workflow node logic
│   ├── state.py                # AgentState schema + working-memory rules
│   ├── llm_client.py           # Anthropic Claude client (intent classify + reply gen)
│   ├── deterministic_router.py # Keyword fallback routing + confirmation parsing
│   └── logging_utils.py        # Structured trace logging
├── tools/                      # The four required tools + shared helpers
│   ├── info_tool.py             # Information tool — grounded menu/FAQ lookup
│   ├── availability_tool.py     # Analysis tool — table/time conflict checking
│   ├── booking_tool.py          # Action tool — create/modify/cancel a reservation
│   ├── reporting_tool.py        # Reporting tool — structured booking summaries
│   ├── data_loader.py           # Loads/caches the static JSON domain data
│   ├── db_helper.py             # SQLite connection + query helpers
│   └── preferences_store.py     # Long-term (bonus) returning-customer memory
├── data/                        # Static domain data
│   ├── menu.json
│   ├── faqs.json
│   └── tables.json
├── db/
│   ├── schema.sql               # Table definitions
│   ├── init_db.py               # DB init/seed/reset script
│   └── bookings.db              # SQLite file (git-ignored in practice; generated)
├── evaluation/                  # 20-case evaluation suite
│   ├── test_cases.json          # The 20 documented test conversations
│   ├── eval_runner.py           # Runs the suite against the live agent
│   ├── eval_metrics.py          # Computes task-completion/tool-selection/fallback metrics
│   ├── eval_report_template.md  # Template for the written evaluation report
│   └── show_failures.py         # Prints just the failing cases for quick debugging
├── tests/                       # Unit tests (pytest) — one file per module under test
│   ├── test_availability_tool.py
│   ├── test_booking_tool.py
│   ├── test_info_tool.py
│   ├── test_reporting_tool.py
│   ├── test_deterministic_router.py
│   ├── test_llm_client_retry.py
│   ├── test_nodes.py
│   ├── test_preferences_store.py
│   ├── test_state.py
│   └── test_helpers.py
├── scripts/
│   └── manual_smoke_test.py     # Quick manual end-to-end check outside pytest
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Installation & Usage

### With Docker (recommended — one command)

```bash
cp .env.example .env      # then add your real ANTHROPIC_API_KEY
docker compose up --build
```

Open **http://localhost:7860** in a browser.

- `docker compose down` stops the app and keeps your data.
- `docker compose down -v` also wipes the database/log volumes for a clean slate.

### Without Docker (local development)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then add your real ANTHROPIC_API_KEY
python db/init_db.py
python app.py
```

### Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | From [console.anthropic.com](https://console.anthropic.com) |
| `ANTHROPIC_MODEL` | No | `claude-haiku-4-5` | |
| `APP_PORT` | No | `7860` | Host port the UI is served on |

`.env` is git-ignored and never baked into the Docker image — only `.env.example` (a placeholder) is committed.

## Testing & evaluation

```bash
pytest tests/                        # unit tests (node logic, tools, router — no API key needed)
python evaluation/eval_runner.py     # runs the 20-case conversational evaluation suite (needs ANTHROPIC_API_KEY)
python evaluation/eval_metrics.py    # prints task-completion, tool-selection, and fallback-accuracy metrics
```

`eval_runner.py` is resumable: it skips any test case already marked `pass` in `evaluation/results.json`, so it's safe to re-run after fixing something (`--retry-failed`) or start completely fresh (`--redo-all`).

The 20 test cases cover grounded info questions, valid/invalid booking inputs, successful and rejected actions, memory across turns, missing/ambiguous info, unsupported requests, and duplicate/conflicting actions (e.g. double-booking the same table, cancelling an already-cancelled reservation).

## Known limitations

- Long-term customer preferences are matched on name alone (no login/phone verification), so two different guests sharing a first name would share one visit history. Documented as a deliberate, acceptable simplification for a course project.
- Short-term/working memory resets on container restart (in-process `MemorySaver`); only long-term preferences and booking records persist, via the `db_data` named volume.
- The information tool matches on whole words with common stopwords filtered out, not fuzzy/semantic search — a very generic query like "what's on the menu" (with no specific dish or category named) may return fewer results than a more specific one, since individual dish records don't literally contain the word "menu."

## AI development tools disclosure

> Claude (Anthropic) was used throughout development for: architecture discussion and design decisions, debugging (including LangGraph orchestration issues, Docker/environment configuration, and a keyword-matching bug in the information tool), writing/refining the evaluation suite, and drafting this README. All team members reviewed and understood the resulting code and can explain any part of it, as required.

