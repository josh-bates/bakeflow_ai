# BAKE-27: continuous integration

This story adds GitHub Actions checks to every pull request and protects `main`, so a broken change cannot be merged. It supports Stage 2 criterion 4 (advanced technologies) and architecture §6.3.

## What runs

**Backend** (`.github/workflows/backend.yml`, job `test`) runs on Ubuntu 24.04 with Python 3.13 and a PostgreSQL 17 service:
1. Install `backend/requirements-dev.txt`. All dependencies are exact pins.
2. Lint: `ruff check .` using the rules in `backend/pyproject.toml`.
3. Test: `pytest`. Unit tests always run. Integration tests run because CI sets `BAKEFLOW_TEST_DATABASE_URL`, and they rebuild the schema with the Alembic migrations, so a broken migration fails CI.

**Frontend** (`.github/workflows/frontend.yml`, job `check`) runs on Node 22 with pnpm:
1. `pnpm install --frozen-lockfile`
2. `pnpm test` (Vitest)
3. `pnpm build`, which runs the strict TypeScript check (`tsc --noEmit`) and then the Vite build.

Neither workflow uses secrets or contacts an LLM provider. Agent tests use `MockLLMClient` and fixtures.

## Decisions

- **Lint rules are errors only.** The rules are `E4`, `E7`, `E9` and `F`: undefined names, unused imports, syntax and runtime errors. Ruff 0.17's broader default set flagged 113 style issues, such as `Decimal("0")` versus `Decimal(0)`. Fixing those would have touched every module for no behavioural gain, so they are off. The one real finding, an unused import in `tests/integration/test_intake_stock_check.py`, is fixed here.
- **Frontend lint is the strict TypeScript check.** `tsconfig.json` has `strict: true`, and `pnpm build` fails on type errors. ESLint is not added this sprint.
- **No path filters.** Previously each workflow ran only when its own folder changed. A required check that never starts blocks the merge, so docs-only PRs could not have been merged. Both workflows now run on every PR. They take about one minute each.
- **The frontend workflow is named "Frontend"** instead of "BAKE-10 frontend", because it is no longer specific to one story.

## Protecting `main`

GitHub refuses branch protection and rulesets on a private repository on the Free plan (HTTP 403). The repository owner will enable GitHub Pro through the GitHub Student Developer Pack. Then, in **Settings → Branches → Add branch ruleset** (or classic branch protection) for `main`:

- Require a pull request before merging, with **1 approving review**.
- Require these status checks to pass: **Backend / test**, **Frontend / check** and **Docker / build** (added by BAKE-29).
- Require branches to be up to date before merging.
- Do not allow bypassing the above settings.

Until protection is on, the team applies the same rule by agreement: checks pass and one review before merging.

## Verification and remaining gates

- [x] `ruff check .` passes locally (ruff 0.17.1). The Backend and Frontend checks ran and passed on #13, which Carl reviewed and approved before it was merged.
- [x] `main` is protected (23 October 2026) by the active ruleset "Protect main branch". It requires the `test` and `check` status checks with the branch up to date, and one approving review. On 25 October the Docker `build` check from BAKE-29 was added, so all three must pass. It blocks force-pushes and deletion, and nobody can bypass it.
- [x] Manual test from the story: [#17](https://github.com/josh-bates/bakeflow_ai/pull/17) added a deliberately failing test. The Backend `test` check failed (1 failed, 240 passed), and GitHub reported the PR as blocked. It was closed without merging, and the closed PR is the evidence.
