# BAKE-44: run the marketing flow from an owner Marketing page

BAKE-25, -26, -35, -36 and -40 built the marketing services, but nothing reached them over HTTP, so none of UC-07 or UC-08 could be shown in the browser. This adds owner-only routes and a Marketing page that runs the whole flow.

## API (`backend/app/marketing/routes.py`)

- **Owner only:** `/api/marketing` is in BAKE-14's owner prefixes, so the middleware checks the session, role and CSRF token, and each handler also declares `require_owner`.
- **Reads:** `GET /summary`, `GET /campaigns` (each campaign's latest version with its approval and posts), `GET /feed` (what the mock channel published).
- **Campaigns:** `POST /campaigns` plans one; `approve`, `reject` and `revise` act on exactly the version in the path.
- **Posts:** `POST /campaigns/{id}/versions/{v}/content` writes one; `approve`, `reject`, `revise`, `calendar`, `export`, `opt-in` and `publish` act on that version. `POST /publish-due` publishes what is due.
- **Refusals:** the services decide. A route returns their status (such as `CampaignRevised` or `NotOptedIn`) with a plain sentence in `detail`.
- **Mock channel:** one shared instance, so the feed survives between requests. No real accounts or tokens.

## Page (`frontend/src/owner/MarketingPage.tsx`)

- **Summary:** profit, top products by units, stock and free capacity, from the server.
- **Plans:** the owner can ask for several plans and compare them side by side, approve one and reject the rest. Rejected, withdrawn and finished plans fold into "Earlier plans".
- **Posts:** each post shows its status (Waiting for you, Approved, In the calendar, Publishing, Published, Publish failed, Rejected, Plan changed). Rejected posts and posts for an older plan version fold into "Earlier posts".
- **Next step:** approve, place in the calendar, export, opt in and publish, with the server's reason shown when it refuses.

## Known limits

- Only each post's latest version is listed, so a post replaced by "Write it again" is not shown.
- Rejected plans stay in the database by design (versioned and audited); the page folds them away.

## Verification

- `backend/tests/integration/test_marketing_routes.py`: the routes, owner-only access and refusals.
- `frontend/src/test/MarketingPage.test.tsx`: the page's flow and folding.
- Full suites on this branch: backend 873 passed, frontend 118 passed.
