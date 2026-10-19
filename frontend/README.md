# BAKE-10 browser interface

A small React/TypeScript interface: one customer page and four owner placeholder
pages. No UI library, router library, external font, AI SDK or business calculations.
The backend determines the current user and filters their orders.

## Run

Use Node.js 22.12+ and pnpm 11.25.0.

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open the local address printed by Vite. `/api` requests are forwarded to the
backend on `127.0.0.1:8000`. Without a backend the page reports that the service
is unavailable; it never invents a signed-in user or successful order.

```sh
pnpm test
pnpm build
```

`dist/` is the deployment output. BAKE-6 should serve these files from FastAPI,
with `/api` reserved for backend routes. Hash navigation avoids a server-side
fallback for every owner page. Register API routes before the static mount and
return JSON errors for unknown `/api` URLs, not the frontend HTML. No production
Node process is needed after building. No API keys belong in the frontend.

## Integration status

At implementation time `main` had no FastAPI application, authentication HTTP
routes or order HTTP routes. The contract in
[BAKE-10 interface handoff](../docs/bake-10-interface.md) is therefore a **proposal**,
not an existing backend API. BAKE-6/11/14 owners must agree and implement/map it
before full acceptance. This PR does not implement those business stories.

Automated component tests simulate HTTP responses to test rendering, navigation,
submission and failures. They are not evidence of database persistence or backend
access control. The screenshot fixture server is likewise a **test-only** backend.

## Screenshot walkthrough (no LLM key required)

After building, run `python3 scripts/preview_fixture.py --role Customer`, then open
`http://127.0.0.1:4175`. Test the form, refresh orders and capture the customer
page. Stop it and run with `--role Owner` to navigate approvals, orders, inventory
and schedules. The fixture returns an intake acknowledgement only and never
saves or confirms an order. Do not use this server as the assessed deployment.

For final acceptance repeat these flows against the integrated backend, verify
that each customer sees only their own orders, attach screenshots to BAKE-10,
and obtain a teammate's PR review before merging.
