# BakeFlow AI

An AI-assisted back-office system for a home-based bakery, developed for **ELEC5620 — Model-Based Software Engineering, University of Sydney**.

BakeFlow AI helps a bakery owner manage custom cake and cupcake enquiries, orders, ingredient stock, production, quotations, bookkeeping and marketing. Four specialist AI agents interpret requests and propose outcomes; deterministic services validate business data, perform calculations and coordinate workflows. The owner retains approval over important decisions.

**Status:** Stage Two implementation in progress. The working stack (architecture §3, OD-1–OD-3) is Python with FastAPI, SQLAlchemy and Alembic, PostgreSQL via Docker Compose, and a React + TypeScript + Vite frontend served by the backend. The backend skeleton runs locally; the frontend and the LLM provider are not in place yet.

## Planned capabilities

| Module | Stage One requirements | Use cases | Stage One lead |
| --- | --- | --- | --- |
| Sales and Customer Service | AR-01 | UC-01: submit a custom order; UC-02: track, change or cancel an order | Ziyi Wang |
| Inventory and Production | AR-02, IP-01–IP-05 | UC-03: check inventory and create a shopping list; UC-04: create and approve a production schedule | Chuanyang Wang |
| Pricing and Bookkeeping | AR-03 | UC-05: generate and approve a quotation; UC-06: record transactions and review profitability | Joshua Bates |
| Marketing | AR-04 | UC-07: plan a campaign; UC-08: generate and approve marketing content | Angelina |

These are the report's design responsibilities, not a claim about completed implementation or future contribution percentages.

## Project references

- [Stage One design baseline](docs/design-baseline.md)
- [Stage Two assessment and evidence](docs/stage-two.md)
- [Stage Two architecture proposal (draft, pending team review)](docs/architecture.md)
- [Jira handoff and initial backlog](docs/jira-handoff.md)
- [Contribution workflow](CONTRIBUTING.md)
- [Source material inventory](docs/source-materials.md)
- [Original Stage One report](docs/reference/stage-1-report.pdf)
- [Original Stage Two marking criteria](docs/reference/stage-2-marking-criteria.pdf)

Jira (project BAKE) holds the live backlog, sprint commitments, estimates and collaboration evidence. GitHub holds version history and reviewed changes.

## Scope

The prototype uses simulated business data, browser interfaces, one application host, a shared SQL database and a server-side gateway to an external LLM. Payment execution, ingredient purchasing, physical baking, delivery routing and automatic social-media publication are outside the automated prototype. Approved marketing content is exported for manual publication.

## Repository setup

GitHub repository: [josh-bates/bakeflow_ai](https://github.com/josh-bates/bakeflow_ai), created as **private**. Display name: **BakeFlow AI**. GitHub description:

> ELEC5620 Stage Two: AI-assisted bakery operations with four specialist agents, deterministic workflows and owner approvals.

See [repository setup and Jira handoff](docs/repository-setup.md).

## Run locally

You need Docker Desktop (Compose v2). To run the backend tests without Docker you need Python 3.11 or newer; the image uses 3.13.

### App and database

```bash
cp .env.example .env    # then set your own POSTGRES_PASSWORD
docker compose up --build -d
docker compose ps       # app and db should both become "healthy"
```

- <http://localhost:8000/api/health/ready> returns `{"status":"ok","database":"ok"}` once the app can reach PostgreSQL.
- <http://localhost:8000/docs> shows the generated API documentation.
- The app applies Alembic migrations when it starts. The database is reachable only from the app container, and the app port is bound to `127.0.0.1`.
- `docker compose logs app` shows the app log. `docker compose down` stops everything; add `-v` to also delete the database volume.

### Backend tests

```bash
cd backend
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

Unit tests need no database. Dependencies are pinned exactly in `backend/requirements.txt` (runtime) and `backend/requirements-dev.txt` (tests); change pins deliberately in their own PR.

### Backend layout

| Path | Contents |
| --- | --- |
| `backend/app/main.py` | FastAPI app: `/api/health`, `/api/health/ready`, and the built React app when `frontend/dist` exists |
| `backend/app/config.py` | Settings from `BAKEFLOW_*` environment variables |
| `backend/app/db/` | SQLAlchemy declarative base, engine and session factory |
| `backend/alembic/` | Migrations; the database URL comes from `BAKEFLOW_DATABASE_URL` |
| `backend/app/<module>/` | Business and support modules, e.g. `pricing_bookkeeping/` |
| `Dockerfile`, `compose.yaml` | One app image plus PostgreSQL (architecture §7.1) |


## LLM provider configuration

All agents reach the language model through one gateway
(`backend/app/llm/gateway.py`, BAKE-9). It is configured from the environment,
never from a committed file:

| Variable | Purpose |
| --- | --- |
| `BAKEFLOW_LLM_BASE_URL` | OpenAI-compatible endpoint |
| `BAKEFLOW_LLM_API_KEY` | provider key |
| `BAKEFLOW_LLM_MODEL` | model name |

Set these in a local `.env` or in your shell. `.env` is git-ignored and must
never be committed or included in the Canvas zip.

Sprint 1 runs entirely on `MockLLMClient`, so no provider is needed to run the
tests or an offline demo. **Provider choice is still open** — the architecture
draft proposes campus Qwen via the AI Proxy first, with a personal subscription
as a fallback. This section will be updated once the team confirms it.

The gateway makes exactly one provider call per request and does not retry. The
retry budget belongs to `WorkflowService` (BAKE-8).
