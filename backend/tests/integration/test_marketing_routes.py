"""The owner's marketing flow over HTTP.

The real app with BAKE-14 sign-in and CSRF, on PostgreSQL, in Mock mode. The
service rules are tested in ``test_campaign_planning.py``,
``test_content_generation.py``, ``test_content_export.py`` and
``test_content_publishing.py``; these tests check the routes, their permissions
and what the owner is told.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.marketing.publishing import ChannelError, MockSocialChannel
from app.shared.records import Campaign, MarketingContent, MockPublication

START = date.today() + timedelta(days=7)
END = START + timedelta(days=6)
PLAN = {"planningStart": START.isoformat(), "planningEnd": END.isoformat()}
GOES_OUT_AT = (datetime.now(UTC) + timedelta(days=8)).isoformat()
POST = {"platform": "Instagram", "scheduledAt": GOES_OUT_AT}


@pytest.fixture
def app(database_url, seeded):
    return create_app(Settings(_env_file=None, database_url=database_url,
                               environment="test", app_secret="test-secret-" * 4))


def login(client, role="owner"):
    response = client.post("/api/auth/login", json={"email": f"{role}@bakeflow.test",
                           "password": f"{role}-test-password"}, headers={"X-Requested-With": "BakeFlow"})
    assert response.status_code == 204, response.text
    return {"X-CSRF-Token": client.get("/api/session").json()["csrfToken"]}


def only_campaign(client) -> dict:
    campaigns = client.get("/api/marketing/campaigns").json()["campaigns"]
    assert len(campaigns) == 1
    return campaigns[0]


def planned(client, owner, *, approve=True) -> str:
    """A campaign's versioned URL, approved unless asked otherwise."""
    made = client.post("/api/marketing/campaigns", json=PLAN, headers=owner)
    assert made.status_code == 201, made.text
    base = f"/api/marketing/campaigns/{made.json()['id']}/versions/{made.json()['version']}"
    if approve:
        assert client.post(f"{base}/approve", headers=owner).status_code == 200
    return base


def written(client, owner, campaign_base, *, approve=True, calendar=True) -> str:
    """A post's versioned URL for an approved campaign."""
    made = client.post(f"{campaign_base}/content", json=POST, headers=owner)
    assert made.status_code == 201, made.text
    base = f"/api/marketing/content/{made.json()['id']}/versions/{made.json()['version']}"
    if approve:
        assert client.post(f"{base}/approve", headers=owner).status_code == 200
    if calendar:
        assert client.post(f"{base}/calendar", headers=owner).status_code == 200
    return base


def test_only_the_owner_reaches_marketing(app):
    with TestClient(app) as client:
        assert client.get("/api/marketing/campaigns").status_code == 401
        customer = login(client, "customer")
        assert client.get("/api/marketing/campaigns").status_code == 403
        assert client.post("/api/marketing/campaigns", json=PLAN, headers=customer).status_code == 403


def test_a_save_needs_the_csrf_token(app):
    with TestClient(app) as client:
        login(client)
        assert client.post("/api/marketing/campaigns", json=PLAN,
                           headers={"X-Requested-With": "BakeFlow"}).status_code == 403


def test_summary_shows_what_a_plan_is_built_from(app):
    with TestClient(app) as client:
        login(client)
        shown = client.get("/api/marketing/summary", params=PLAN)
        assert shown.status_code == 200, shown.text
        figures = shown.json()
        assert figures["sufficient"] is False
        assert figures["overall"]["transactions"] == 0
        assert figures["freeMinutes"] > 0


def test_the_last_day_cannot_be_before_the_first(app):
    with TestClient(app) as client:
        owner = login(client)
        backwards = {"planningStart": END.isoformat(), "planningEnd": START.isoformat()}
        assert client.post("/api/marketing/campaigns", json=backwards, headers=owner).status_code == 422
        assert client.get("/api/marketing/summary", params=backwards).status_code == 422


def test_plan_write_approve_export_and_publish(app, store):
    with TestClient(app) as client:
        owner = login(client)

        # UC-07: planned from no history, so it says so, and waits for the owner.
        campaign_base = planned(client, owner, approve=False)
        campaign = only_campaign(client)
        assert campaign["approval"] == "Pending"
        assert "limited business data" in campaign["lowConfidenceNotice"]
        assert campaign["products"] and all(p["indicativePrice"] for p in campaign["products"])

        # No post until the plan is approved.
        early = client.post(f"{campaign_base}/content", json=POST, headers=owner)
        assert early.status_code == 409
        assert early.json()["status"] == "CampaignNotApproved"

        assert client.post(f"{campaign_base}/approve", headers=owner).status_code == 200
        assert client.post(f"{campaign_base}/approve", headers=owner).status_code == 200  # a double click
        assert only_campaign(client)["approval"] == "Approved"

        # UC-08: the post quotes the calculated price and waits for its own approval.
        post_base = written(client, owner, campaign_base, approve=False, calendar=False)
        post = only_campaign(client)["posts"][0]
        price = only_campaign(client)["products"][0]["indicativePrice"]
        assert f"${price}" in post["body"] and "confirmed in your quote" in post["body"]
        assert (post["approval"], post["inCalendar"], post["optedIn"], post["planCurrent"]) == (
            "Pending", False, False, True)

        refused = client.post(f"{post_base}/calendar", headers=owner)
        assert (refused.status_code, refused.json()["status"]) == (409, "NotApproved")
        assert client.post(f"{post_base}/approve", headers=owner).status_code == 200

        too_soon = client.get(f"{post_base}/export")
        assert (too_soon.status_code, too_soon.json()["status"]) == (409, "NotInCalendar")
        assert client.post(f"{post_base}/calendar", headers=owner).status_code == 200
        exported = client.get(f"{post_base}/export")
        assert exported.status_code == 200, exported.text
        assert post["body"] in exported.json()["text"]

        # Publishing is per post and opt-in.
        not_opted = client.post(f"{post_base}/publish", headers=owner)
        assert (not_opted.status_code, not_opted.json()["status"]) == (409, "NotOptedIn")
        assert client.get("/api/marketing/feed").json() == {"posts": []}
        assert client.post(f"{post_base}/opt-in", json={"optedIn": True}, headers=owner).status_code == 200
        assert only_campaign(client)["posts"][0]["optedIn"] is True

        done = client.post(f"{post_base}/publish", headers=owner)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "Published"
        again = client.post(f"{post_base}/publish", headers=owner)
        assert again.json() == done.json() | {"status": "AlreadyPublished"}

        shown = only_campaign(client)["posts"][0]["publication"]
        assert (shown["status"], shown["reference"]) == ("Published", done.json()["reference"])
        [entry] = client.get("/api/marketing/feed").json()["posts"]
        # The post's own words, without the export's "[platform | UTC time]" heading.
        assert (entry["body"], entry["platform"], entry["reference"]) == (
            post["body"], "Instagram", done.json()["reference"])

    with store.unit_of_work() as session:
        assert len(session.scalars(select(MockPublication)).all()) == 1


def test_routes_share_one_channel(app):
    """Each request builds its own publisher; posts still reach the same channel."""
    posted = []
    original = MockSocialChannel.post

    def spy(self, text):
        posted.append(self)
        return original(self, text)

    with pytest.MonkeyPatch.context() as patch, TestClient(app) as client:
        patch.setattr(MockSocialChannel, "post", spy)
        owner = login(client)
        campaign_base = planned(client, owner)
        for _ in range(2):
            post_base = written(client, owner, campaign_base)
            assert client.post(f"{post_base}/opt-in", json={"optedIn": True}, headers=owner).status_code == 200
            assert client.post(f"{post_base}/publish", headers=owner).status_code == 200
    assert len(posted) == 2 and posted[0] is posted[1]
    assert len(posted[0].feed) == 2


def test_asking_again_gives_another_plan_to_compare(app):
    with TestClient(app) as client:
        owner = login(client)
        for _ in range(2):
            assert client.post("/api/marketing/campaigns", json=PLAN, headers=owner).status_code == 201
        campaigns = client.get("/api/marketing/campaigns").json()["campaigns"]
        # Each request is its own plan, waiting for the owner beside the other.
        assert [c["approval"] for c in campaigns] == ["Pending", "Pending"]
        assert campaigns[0]["id"] != campaigns[1]["id"]


def test_rejected_plan_can_be_planned_again(app, store):
    with TestClient(app) as client:
        owner = login(client)
        base = planned(client, owner, approve=False)
        assert client.post(f"{base}/reject", headers=owner).status_code == 200
        assert only_campaign(client)["approval"] == "Rejected"
        changed = client.post(f"{base}/approve", headers=owner)
        assert changed.status_code == 409 and "already has your decision" in changed.json()["detail"]

        revised = client.post(f"{base}/revise", json=PLAN, headers=owner)
        assert revised.status_code == 201, revised.text
        assert revised.json()["version"] == 2
        latest = only_campaign(client)
        assert (latest["version"], latest["approval"]) == (2, "Pending")
    with store.unit_of_work() as session:
        assert len(session.scalars(select(Campaign)).all()) == 2


def test_revising_the_plan_stops_its_posts_until_they_are_rewritten(app):
    with TestClient(app) as client:
        owner = login(client)
        campaign_base = planned(client, owner)
        post_base = written(client, owner, campaign_base)
        assert client.post(f"{post_base}/opt-in", json={"optedIn": True}, headers=owner).status_code == 200

        assert client.post(f"{campaign_base}/revise", json=PLAN, headers=owner).status_code == 201
        campaign = only_campaign(client)
        assert campaign["posts"][0]["planCurrent"] is False

        for attempt in (client.get(f"{post_base}/export"), client.post(f"{post_base}/publish", headers=owner)):
            assert (attempt.status_code, attempt.json()["status"]) == (409, "CampaignRevised")

        # Rewriting waits for the new plan's approval, and is then a new version, opted out.
        waiting = client.post(f"{post_base}/revise", json=POST, headers=owner)
        assert (waiting.status_code, waiting.json()["status"]) == (409, "CampaignNotApproved")
        new_base = f"/api/marketing/campaigns/{campaign['id']}/versions/2"
        assert client.post(f"{new_base}/approve", headers=owner).status_code == 200
        rewritten = client.post(f"{post_base}/revise", json=POST, headers=owner)
        assert rewritten.status_code == 201, rewritten.text
        assert rewritten.json()["version"] == 2
        post = only_campaign(client)["posts"][0]
        assert (post["version"], post["approval"], post["optedIn"], post["planCurrent"]) == (2, "Pending", False, True)


def test_an_old_version_cannot_be_acted_on(app):
    with TestClient(app) as client:
        owner = login(client)
        base = planned(client, owner)
        assert client.post(f"{base}/revise", json=PLAN, headers=owner).status_code == 201
        stale = client.post(f"{base}/revise", json=PLAN, headers=owner)
        assert stale.status_code == 409 and "reload" in stale.json()["detail"]
        missing = "/api/marketing/campaigns/00000000-0000-0000-0000-000000000000/versions/1/approve"
        assert client.post(missing, headers=owner).status_code == 404


def test_a_channel_failure_is_explained_and_the_post_stays_exportable(app, monkeypatch):
    def refuse(self, text):
        raise ChannelError("the channel is down")

    with TestClient(app) as client:
        owner = login(client)
        post_base = written(client, owner, planned(client, owner))
        assert client.post(f"{post_base}/opt-in", json={"optedIn": True}, headers=owner).status_code == 200

        with monkeypatch.context() as patch:
            patch.setattr(MockSocialChannel, "post", refuse)
            failed = client.post(f"{post_base}/publish", headers=owner)
        assert (failed.status_code, failed.json()["status"]) == (503, "ChannelFailed")
        assert "posted by hand" in failed.json()["detail"]
        shown = only_campaign(client)["posts"][0]["publication"]
        assert (shown["status"], shown["error"]) == ("Failed", "the channel is down")
        assert client.get(f"{post_base}/export").status_code == 200

        # A retry after a failure is free to try again.
        assert client.post(f"{post_base}/publish", headers=owner).status_code == 200


def test_publish_due_publishes_only_opted_in_posts_whose_time_has_come(app, store):
    with TestClient(app) as client:
        owner = login(client)
        campaign_base = planned(client, owner)
        future = written(client, owner, campaign_base)
        past = {"platform": "Facebook", "scheduledAt": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()}
        made = client.post(f"{campaign_base}/content", json=past, headers=owner)
        due_base = f"/api/marketing/content/{made.json()['id']}/versions/1"
        assert client.post(f"{due_base}/approve", headers=owner).status_code == 200
        assert client.post(f"{due_base}/calendar", headers=owner).status_code == 200

        assert client.post("/api/marketing/publish-due", headers=owner).json() == {"published": [], "failed": []}
        for base in (future, due_base):
            assert client.post(f"{base}/opt-in", json={"optedIn": True}, headers=owner).status_code == 200

        first = client.post("/api/marketing/publish-due", headers=owner).json()
        assert [p["id"] for p in first["published"]] == [made.json()["id"]]
        assert client.post("/api/marketing/publish-due", headers=owner).json() == {"published": [], "failed": []}
    with store.unit_of_work() as session:
        assert len(session.scalars(select(MarketingContent)).all()) == 2


def test_no_usable_assistant_saves_nothing(database_url, seeded, store, monkeypatch):
    for name in ("BAKEFLOW_LLM_API_KEY", "BAKEFLOW_LLM_BASE_URL", "BAKEFLOW_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    live = create_app(Settings(_env_file=None, database_url=database_url, environment="test",
                               app_secret="test-secret-" * 4, llm_mode="live"))
    with TestClient(live) as client:
        owner = login(client)
        failed = client.post("/api/marketing/campaigns", json=PLAN, headers=owner)
        assert (failed.status_code, failed.json()["status"]) == (503, "AIUnavailable")
        assert "nothing was saved" in failed.json()["detail"]
        assert client.get("/api/marketing/campaigns").json() == {"campaigns": []}
