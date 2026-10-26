# BAKE-29: clean-zip deployment rehearsal

At the Week 13 demonstration the team must download the submitted source zip from Canvas, extract it into a fresh folder and start the app from it (architecture §7.2, OD-7). This story proves that path early. It covers check **T-CLEAN-ZIP** (architecture §6.3): from a clean zip and a privately prepared `.env`, one Compose command starts the app and database, migrations run, seed/reset works and the UI core journeys run.

## Rehearsal 1: 24 October 2026 (Sprint 2)

Source: `main` at `a8a8a49` (PRs #11, #13, #15, #16 merged). Machine: Carl's Windows 11 laptop, Docker Desktop with Compose v2.

### Steps

1. Build the archive from the commit, not from the working folder, so untracked and ignored files cannot leak in:

   ```bash
   git archive --format=zip --prefix=bakeflow_ai/ -o bakeflow_ai.zip HEAD
   ```

   The archive is 7.6 MB with 145 entries and one top-level `bakeflow_ai/` folder. It contains `.env.example` but no `.env`, `.venv`, `node_modules` or `frontend/dist`. The largest file is `docs/reference/stage-1-report.pdf` (8 MB uncompressed).
2. Extract it into a new, empty folder outside the repository.
3. Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`, `BAKEFLOW_ENVIRONMENT=demo`, a generated `BAKEFLOW_APP_SECRET` and both seed passwords. The LLM variables stay empty, so the app runs on Mock.
4. `docker compose -p bakeflow-zip up --build -d`. The `-p` flag kept this run apart from the development stack on the same laptop (see gap 4). On demo day the command is the plain `docker compose up --build -d`.
5. `docker compose exec app python -m app.seed`, then the reset and restart checks below.

### Results

| Check | Result |
| --- | --- |
| Build and start | Passed. 34 s with network and no matching layer cache; 16 s with `--no-cache` once the base images were local. `app` and `db` both reached `healthy`. |
| Port binding | `127.0.0.1:8000` only [R15]. The database publishes no port. |
| Migrations | Applied on startup up to `bake11_disclosures`, which is the only head. |
| Seed | `python -m app.seed` created 2 accounts, 1 customer, 1 bakery setting, 8 inventory items, 4 products, 4 recipes and 28 recipe ingredients. |
| Seed guards | A second plain seed was refused ("already has data"). `--reset` without `--yes` was refused. `--reset --yes` re-seeded the same counts. |
| Restart | `docker compose down`, then `up --build -d`: data kept, no migration re-run, seed still refused. |
| Health | `/api/health/ready` returned `{"status":"ok","database":"ok"}`. `/docs` returned 200. |
| UI | The built SPA loads at `http://localhost:8000/`, and client routes such as `/owner` fall back to it. It stops at the sign-in page (gap 1). |
| Line endings | Git for Windows sets `core.autocrlf=true`, so `git archive` wrote CRLF line endings in 101 files, and the `.env` copied from `.env.example` was CRLF too. The image built and Compose read every value without a trailing `\r`. No change is needed. |

The rehearsal containers, volume and image were removed afterwards.

### Gaps found

1. **No sign-in on `main` yet, so the UI journeys cannot run.** `/api/session` always answers 401, and the page's "Sign in" link goes to `/api/auth/login`, which is 404. Both arrive with BAKE-14 (#14). Rerun this check once #14 is merged, and again as BAKE-15, 21 and 23 land, because T-CLEAN-ZIP requires the seeded journeys of §7.2 step 5.
2. **The README has no zip or demo-day procedure.** Architecture §6.1 says the README holds "build, seed/reset, zip/demo procedure". It needs: how to build the archive, extracting into a fresh folder, the `.env` values for a demo (`BAKEFLOW_ENVIRONMENT=demo`, and `BAKEFLOW_APP_SECRET` being required outside `development`, which today appears only under the BAKE-11 heading), seed versus reset, smoke checks, and how to switch to labelled Mock.
3. **`.env.example` omits `BAKEFLOW_LLM_MODE`.** `app/config.py` accepts `mock` or `live` and defaults to `mock`. Architecture §7.1 requires an explicit mode switch, and §7.2 step 6 depends on switching to Mock on site, so the variable should be listed with its values.
4. **The fixed Compose project name shares one database volume across folders.** `compose.yaml` sets `name: bakeflow`, so every extracted copy uses the same `bakeflow_db-data` volume and container names as the development checkout. If that volume was last migrated by an unmerged branch, for example one carrying `bake14_sessions`, the submitted code fails at `alembic upgrade head` with "Can't locate revision". Seeding also refuses because data already exists. The demo procedure should begin with `docker compose down -v`, or the README should say so for the final rehearsal.
5. **Architecture §7.1 shows `python -m app.seed --reset` without `--yes`.** The CLI rejects that. The README is correct; the architecture document should be synced.

### Not verified yet

- **An offline build.** The build contacts Docker Hub for the base image metadata, then PyPI and the npm registry. §7.2 step 2 assumes a prebuilt cache makes the on-site build work offline. Test it on the demo laptop with Wi-Fi off, after one cached build.
- **Base image pins.** The build resolved `node:22-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d` and `python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39`. Pinning these digests in the `Dockerfile` (Josh's suggestion on #11) is still open.
- **Secure cookies over loopback HTTP** with `BAKEFLOW_ENVIRONMENT=demo`, which needs #14.
- **The real LLM path**, which waits for the provider decision (OD-3).

## Still to do in BAKE-29

- [x] Fix gaps 2–4: the README's new "Submission zip and demo day" section (archive command, demo `.env` values, `docker compose down -v`, switching to Mock) and `BAKEFLOW_LLM_MODE` in `.env.example`.
- [x] Add a CI job that builds the Docker image, so a broken `Dockerfile` fails a PR instead of the demo. `.github/workflows/docker.yml` (job `build`) copies `.env.example` to `.env` with demo settings and throwaway values, builds with `docker compose build app`, starts with `docker compose up -d --wait`, checks `/api/health/ready` and `/`, then runs the seed and `--reset --yes`. Run locally with the same steps, it passed in 41 s. A broken `COPY` path failed the build step. A broken `CMD` built fine but failed the start step, which is why the job starts the stack instead of only building it.
- [x] Ask the repository owner to add **Docker / build** to the required checks for `main`. Added on 25 October 2026 (Josh).
- [ ] Rehearse again after #14, then from the actual candidate submission archive before 1 November (§7.2 step 7).
