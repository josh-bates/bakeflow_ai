# BakeFlow AI

An AI-assisted back-office system for a home-based bakery, developed for **ELEC5620 — Model-Based Software Engineering, University of Sydney**.

BakeFlow AI helps a bakery owner manage custom cake and cupcake enquiries, orders, ingredient stock, production, quotations, bookkeeping and marketing. Four specialist AI agents interpret requests and propose outcomes; deterministic services validate business data, perform calculations and coordinate workflows. The owner retains approval over important decisions.

**Status:** Stage Two implementation in progress. The backend skeleton uses FastAPI, SQLAlchemy, Alembic and PostgreSQL via Docker Compose. The BAKE-10 React/TypeScript/Vite browser shell is available; business endpoint integration is in progress. Sprint 1 uses Mock; the real LLM provider remains undecided.

## Planned capabilities

The BAKE-10 browser interface is now available in [`frontend/`](frontend/README.md).
Its customer page and owner placeholders require the proposed backend endpoints
in [the interface handoff](docs/bake-10-interface.md). Frontend tests use simulated
responses; end-to-end backend integration is still pending.

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
- [Sprint 2 plan: Pricing and Bookkeeping](docs/sprint-2-pricing-bookkeeping-plan.md)
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

You need Docker Desktop (Compose v2). Use Python 3.13 for backend tests to match the Docker image and CI.

### App and database

```bash
cp .env.example .env    # then set your own POSTGRES_PASSWORD and seed passwords
docker compose up --build -d
docker compose ps       # app and db should both become "healthy"
docker compose exec app python -m app.seed    # demo data into the empty database
```

To start the demo data again from scratch: `docker compose exec app python -m app.seed --reset --yes`. This deletes all business data, so it is refused unless `BAKEFLOW_ENVIRONMENT` is `development`, `demo` or `test`. The seed creates the logins `owner@bakeflow.test` and `customer@bakeflow.test` with the passwords from `.env`.

- <http://localhost:8000/> shows the UI. The image builds it from `frontend/` with Node 22 and pnpm, so you do not need Node installed to run the app.
- <http://localhost:8000/api/health/ready> returns `{"status":"ok","database":"ok"}` once the app can reach PostgreSQL.
- <http://localhost:8000/docs> shows the generated API documentation.
- The app applies Alembic migrations when it starts. The database is reachable only from the app container, and the app port is bound to `127.0.0.1`.
- `docker compose logs app` shows the app log. `docker compose down` stops everything; add `-v` to also delete the database volume.

### Submission zip and demo day

At the Week 13 demonstration we download the submitted zip from Canvas, extract it into a new folder and start the app from it (architecture §7.2). Rehearsal results are in [docs/bake-29-clean-zip.md](docs/bake-29-clean-zip.md).

**Build the zip** from a commit, not by zipping the working folder, so `.env`, `.venv`, `node_modules` and other local files cannot get in:

```bash
git archive --format=zip --prefix=bakeflow_ai/ -o bakeflow_ai.zip HEAD
```

**Before the demo**, on the demo laptop: install Docker Desktop, prepare a private `.env` from `.env.example`, and build the image once from the zip so the dependency layers are cached. Keep the `.env` and the demo passwords out of the repository and the zip.

| Variable | Demo setting |
| --- | --- |
| `POSTGRES_PASSWORD` | Your own value, without `@ : / ? # %` |
| `BAKEFLOW_ENVIRONMENT` | `demo` |
| `BAKEFLOW_APP_SECRET` | Required in `demo`, at least 32 bytes: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `BAKEFLOW_SEED_OWNER_PASSWORD`, `BAKEFLOW_SEED_CUSTOMER_PASSWORD` | Your own; keep them in private demo notes |
| `BAKEFLOW_LLM_MODE` | `live` with the three provider settings below, or `mock` |

**On the day**, in the folder extracted from the Canvas download (not the development checkout):

```bash
# copy the prepared .env into this folder first
docker compose down -v      # remove the old demo database; see below
docker compose up --build -d
docker compose ps           # wait until app and db are "healthy"
docker compose exec app python -m app.seed
```

Then open <http://localhost:8000/> and check <http://localhost:8000/api/health/ready>.

`docker compose down -v` is needed because `compose.yaml` fixes the project name to `bakeflow`, so every extracted copy and the development checkout share one database volume. If that volume was last migrated by an unmerged branch, the submitted code stops at `alembic upgrade head` with "Can't locate revision", and the seed refuses a database that already has data. Removing the volume starts from an empty database. It deletes all the data in it, so run it only on the demo laptop. To restart the demo data later, use `docker compose exec app python -m app.seed --reset --yes` instead.

If the LLM provider is unreachable, set `BAKEFLOW_LLM_MODE=mock` in `.env` and run `docker compose up -d --force-recreate app`. The UI then shows the "Mock mode" banner; tell the demonstrator that the responses are simulated.

### Backend tests

```bash
cd backend
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

Unit tests need no database. Integration tests (`tests/integration/`) run against a real, throwaway PostgreSQL and are skipped unless you point them at one:

```bash
docker compose --profile test up -d test-db    # in-memory, 127.0.0.1:55432
BAKEFLOW_TEST_DATABASE_URL=postgresql+psycopg://bakeflow_test:bakeflow_test@127.0.0.1:55432/bakeflow_test pytest
```

They rebuild the schema with the migrations, so never point them at a database you need. Dependencies are pinned exactly in `backend/requirements.txt` (runtime) and `backend/requirements-dev.txt` (tests); change pins deliberately in their own PR.

Lint the backend before pushing with `ruff check .` (from `backend/`, after installing `requirements-dev.txt`).

### Continuous integration

GitHub Actions runs three checks on every pull request and on every push to `main`:

| Workflow | Steps |
| --- | --- |
| **Backend** (`.github/workflows/backend.yml`) | Install the pinned dependencies, `ruff check .`, then `pytest` against a PostgreSQL 17 service. Integration tests apply the Alembic migrations. |
| **Frontend** (`.github/workflows/frontend.yml`) | `pnpm install --frozen-lockfile`, `pnpm test` (Vitest), `pnpm build` (strict TypeScript check + Vite build) |
| **Docker** (`.github/workflows/docker.yml`) | Build the image with `docker compose`, start app and db with demo settings until both are healthy, check `/api/health/ready` and the UI, then seed and reset (BAKE-29) |

The checks need no secrets and never call a live LLM: CI uses the Mock client and fixtures, and the Docker check writes throwaway values into its `.env`. A pull request is merged into `main` only once the checks pass and one teammate has approved it. GitHub enforces this with the `main` ruleset, which requires **Backend / test**, **Frontend / check**, **Docker / build**, one approval and a branch that is up to date with `main` (see [docs/bake-27-ci.md](docs/bake-27-ci.md)).

Why CI matters for this prototype: four people are integrating modules in parallel against shared contracts (records, the workflow, approvals). CI runs the business-rule tests on every change, including the deterministic money, stock and version-approval checks. That is how a change that breaks someone else's module gets caught before it reaches `main` or the Canvas submission. It also leaves a reviewable record that each merged change was tested. See [docs/bake-27-ci.md](docs/bake-27-ci.md).

### Project layout

Each module is a package inside one application, so all four members can work in parallel (architecture §6.1). Put new code in the package that owns it, and import other modules only through `shared/`.

| Path | Contents |
| --- | --- |
| `backend/app/main.py` | FastAPI app: `/api/health`, `/api/health/ready`, and the built UI when `frontend/dist` exists |
| `backend/app/config.py` | Settings from `BAKEFLOW_*` environment variables |
| `backend/app/db/` | SQLAlchemy declarative base, engine and session factory |
| `backend/app/shared/` | Shared model and services: `records/` (Stage 1 core tables), `RecordRef`, `ValidatedRecordStore` (BAKE-7); events, task contracts and approvals to follow |
| `backend/app/seed.py` | Deterministic demo data: `python -m app.seed [--reset --yes]` |
| `backend/app/workflow/` | `WorkflowService` and transition tables (BAKE-8); architecture §6.1 calls it `coordination/` |
| `backend/app/llm/` | LLM gateway and Mock client (BAKE-9); architecture §6.1 calls it `ai_support/` |
| `backend/app/sales/` | Sales and Customer Service: UC-01, UC-02 |
| `backend/app/inventory_production/` | Inventory and Production: UC-03, UC-04, IP-01–IP-05 |
| `backend/app/pricing_bookkeeping/` | Pricing and Bookkeeping: UC-05, UC-06 |
| `backend/app/marketing/` | Marketing: UC-07, UC-08 |
| `backend/alembic/` | Migrations; the database URL comes from `BAKEFLOW_DATABASE_URL` |
| `backend/tests/` | pytest suites |
| `frontend/` | UI: React + TypeScript + Vite browser shell (BAKE-10); its build output `frontend/dist` is served by the backend |
| `Dockerfile`, `compose.yaml` | One app image plus PostgreSQL (architecture §7.1) |


## LLM provider configuration

All agents reach the language model through one gateway
(`backend/app/llm/gateway.py`, BAKE-9). It is configured from the environment,
never from a committed file:

| Variable | Purpose |
| --- | --- |
| `BAKEFLOW_LLM_MODE` | `mock` (default, simulated responses) or `live` (calls the provider) |
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


### BAKE-11 summary signing and optional local demo

Set `BAKEFLOW_APP_SECRET` in your private `.env` to a randomly generated value of at least 32 bytes (see `.env.example`). Use the same value across restarts and workers so pending summaries remain valid until their 30-minute expiry. Changing it invalidates pending summaries. Only `development` permits an empty setting, with a warning and a temporary random key; other environments refuse to start without it.

The optional `backend/requirements-local-demo.txt` installs native PostgreSQL via `pgserver==0.1.4`. Its wheel availability depends on OS and Python version: it was tested here on macOS with Python 3.12, and installation is not available on the reviewed Windows/Python 3.13 setup. If pip reports “No matching distribution”, use the supported Docker Compose instructions above. This optional launcher is not required by the application, CI or Docker deployment.

### BAKE-14 sign-in and permissions

After migrating and seeding, sign in with `customer@bakeflow.test` or
`owner@bakeflow.test` using the seed passwords from your private environment.
Customers can read only their own orders; owner operations require an owner
session. Sign out revokes the session. See [BAKE-14 setup and API contract](docs/bake-14-auth.md)
for CSRF headers, shared permission dependencies and the optional local launcher.
Deployment requires HTTPS; local development may use loopback HTTP.

### Customer order notifications (BAKE-38)

Customer **Order updates** refresh automatically after recorded order transitions. Notifications are private, deduplicated and template-based, and delivery failure cannot undo an order operation. No API key or email configuration is required. This is a new Stage 2 optional feature, absent from the Stage 1 feature diagram; see [design differences, failure recovery and demo steps](docs/bake38-notifications.md).
