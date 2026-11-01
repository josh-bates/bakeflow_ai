import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type MarketingCampaign,
  type MarketingPost,
  type MarketingSummary,
  type PostPlan,
  type PublishedPost,
  type Session,
} from "../api/client";
import { AiMark } from "../components/AiMark";
import { Notice } from "../components/Notice";

// UC-07 and UC-08: the assistant proposes a campaign and writes its posts, and
// the owner approves each one at its version. The server decides what may happen
// next and says why not; the page offers the next step and shows that reason.
// Publishing goes to a local mock channel: nothing leaves this computer.

const ZONE = "Australia/Sydney";
const when = (iso: string) =>
  new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: ZONE }).format(new Date(iso));
const day = (iso: string) =>
  new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short", timeZone: ZONE }).format(new Date(iso));
const localDate = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: ZONE }).format(new Date(iso));
const daysFromNow = (days: number) => localDate(new Date(Date.now() + days * 86_400_000).toISOString());
const hours = (minutes: number) => {
  const whole = Math.floor(minutes / 60);
  return minutes % 60 ? `${whole} h ${minutes % 60} min` : `${whole} h`;
};
const ended = (campaign: MarketingCampaign) => new Date(campaign.endsAt).getTime() < Date.now();
// Rejected posts and posts written for an older version of the plan fold away, unless already out.
const settled = (post: MarketingPost) =>
  (!post.publication || post.publication.status === "Failed") &&
  (!post.planCurrent || post.approval === "Rejected" || post.approval === "Withdrawn");

const BAND: Record<MarketingCampaign["approval"], { tone: string; label: string }> = {
  Pending: { tone: "apricot", label: "Waiting for you" },
  Approved: { tone: "mint", label: "Approved" },
  Rejected: { tone: "blush", label: "Rejected · plan it again" },
  Withdrawn: { tone: "blush", label: "Approval withdrawn · plan it again" },
};

type Note = { scope: string; text: string; error: boolean } | null;
type Act = (scope: string, action: () => Promise<unknown>, done: string | (() => string)) => Promise<void>;

function NoteFor({ note, scope }: { note: Note; scope: string }) {
  if (note?.scope !== scope) return null;
  return note.error ? <Notice error>{note.text}</Notice> : <Notice>{note.text}</Notice>;
}

function PostStatus({ post }: { post: MarketingPost }) {
  if (post.publication?.status === "Published") return <span className="badge" data-status="Completed">Published</span>;
  if (post.publication?.status === "Publishing") return <span className="badge" data-status="InProgress">Publishing</span>;
  if (!post.planCurrent) return <span className="badge" data-status="NeedsRevision">Plan changed</span>;
  if (post.approval === "Pending") return <span className="badge" data-status="AwaitingApproval">Waiting for you</span>;
  if (post.approval !== "Approved") return <span className="badge" data-status="NeedsRevision">{post.approval}</span>;
  if (post.publication?.status === "Failed") return <span className="badge" data-status="Failed">Publish failed</span>;
  return post.inCalendar
    ? <span className="badge" data-status="Scheduled">In the calendar</span>
    : <span className="badge" data-status="Approved">Approved</span>;
}

function Post({ post, campaign, session, busy, act, note }: {
  post: MarketingPost;
  campaign: MarketingCampaign;
  session: Session;
  busy: boolean;
  act: Act;
  note: Note;
}) {
  const [exported, setExported] = useState(false);
  const [copied, setCopied] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const scope = post.id;
  const heading = `${post.platform}${post.scheduledAt ? ` · ${when(post.scheduledAt)}` : ""}`;
  const published = post.publication?.status === "Published";
  const again: PostPlan | null = post.scheduledAt ? { platform: post.platform, scheduledAt: post.scheduledAt } : null;
  const writeAgain = again && (
    <button className="secondary ai" disabled={busy}
      onClick={() => act(scope, () => api.revisePost(post, again, session.csrfToken), `Version ${post.version + 1} of this post is ready for your review.`)}>
      <AiMark />
      Write it again
    </button>
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(post.body);
      setCopied(true);
    } catch {
      area.current?.select();
    }
  }

  return (
    <article className="post" aria-label={`Post: ${heading}`}>
      <div className="post-top">
        <h3>{heading}</h3>
        <PostStatus post={post} />
      </div>
      <p className="post-body">{post.body}</p>
      <p className="field-help">
        Version {post.version}.
        {published && post.publication?.publishedAt && ` Published ${when(post.publication.publishedAt)} as ${post.publication.reference}.`}
      </p>
      {!published && post.publication?.status === "Failed" && (
        <Notice error>
          The last attempt failed: {post.publication.error}. Nothing was published, and you can try again or post it by hand.
        </Notice>
      )}
      {!published && !post.planCurrent && (
        campaign.approval === "Approved" ? (
          <>
            <Notice>The campaign plan changed after this post was written, so it can’t go out as it is.</Notice>
            <div className="actions">{writeAgain}</div>
          </>
        ) : (
          <Notice>The campaign plan changed after this post was written. Approve the new plan, then write the post again.</Notice>
        )
      )}
      {!published && post.planCurrent && post.approval === "Pending" && (
        <div className="actions">
          <button className="primary" disabled={busy}
            onClick={() => act(scope, () => api.decidePost(post, "approve", session.csrfToken), "Post approved. Add it to the calendar when you’re ready.")}>
            Approve post
          </button>
          <button className="secondary" disabled={busy}
            onClick={() => act(campaign.id, () => api.decidePost(post, "reject", session.csrfToken), "Post rejected and moved to Earlier posts. Nothing will go out.")}>
            Reject
          </button>
        </div>
      )}
      {!published && post.planCurrent && (post.approval === "Rejected" || post.approval === "Withdrawn") && (
        <div className="actions">{writeAgain}</div>
      )}
      {!published && post.planCurrent && post.approval === "Approved" && !post.inCalendar && (
        <div className="actions">
          <button className="primary" disabled={busy}
            onClick={() => act(scope, () => api.placePost(post, session.csrfToken), "Post added to the calendar.")}>
            Add to calendar
          </button>
        </div>
      )}
      {!published && post.planCurrent && post.approval === "Approved" && post.inCalendar && (
        <>
          <div className="actions">
            <label className="opt-in">
              <input type="checkbox" checked={post.optedIn} disabled={busy}
                onChange={(e) => {
                  const optedIn = e.target.checked;
                  act(scope, () => api.setPostOptIn(post, optedIn, session.csrfToken),
                    optedIn ? "Publishing is on for this version." : "Publishing is off for this version.");
                }} />
              Allow this post to be published
            </label>
            <button className="primary" disabled={busy || !post.optedIn}
              onClick={() => act(scope, () => api.publishPost(post, session.csrfToken), "Published to the mock channel.")}>
              Publish now
            </button>
            <button className="secondary" disabled={busy}
              onClick={() => act(scope, async () => {
                await api.exportPost(post);
                setCopied(false);
                setExported(true);
              }, "Here’s the text to post by hand.")}>
              Post it by hand
            </button>
          </div>
          {exported && (
            <div className="post-export">
              <label>
                Text for {post.platform}{post.scheduledAt ? `, ${when(post.scheduledAt)}` : ""}
                <textarea ref={area} readOnly value={post.body} />
              </label>
              <div className="actions">
                <button className="secondary" onClick={copy}>{copied ? "Copied" : "Copy text"}</button>
              </div>
            </div>
          )}
        </>
      )}
      <NoteFor note={note} scope={scope} />
    </article>
  );
}

function CampaignCard({ campaign, session, busy, act, note, rival }: {
  campaign: MarketingCampaign;
  session: Session;
  busy: boolean;
  act: Act;
  note: Note;
  /** An approved plan still running, which a plan waiting beside it would run alongside. */
  rival?: MarketingCampaign;
}) {
  const [platform, setPlatform] = useState<PostPlan["platform"]>("Instagram");
  const [time, setTime] = useState(`${localDate(campaign.startsAt)}T10:00`);
  const state = BAND[campaign.approval];
  const plan = { start: localDate(campaign.startsAt), end: localDate(campaign.endsAt) };
  const over = ended(campaign);
  const live = campaign.posts.filter((p) => !settled(p));
  const older = campaign.posts.filter(settled);
  const post = (item: MarketingPost) => (
    <Post key={`${item.id}-${item.version}`} post={item} campaign={campaign} session={session} busy={busy} act={act} note={note} />
  );

  function write(event: FormEvent) {
    event.preventDefault();
    if (busy || !time) return;
    act(campaign.id, () => api.writePost(campaign, { platform, scheduledAt: new Date(time).toISOString() }, session.csrfToken),
      "The post is written and waiting for your approval.");
  }

  return (
    <section className="card schedule" aria-labelledby={`campaign-${campaign.id}`}>
      <div className={`lid ${state.tone}`}>
        <span className="caps">{state.label}</span>
        <span className="caps">Version {campaign.version}</span>
      </div>
      <div className="plan-body">
        <h2 id={`campaign-${campaign.id}`}>{campaign.objective}</h2>
        <p className="field-help">{over ? "Ran" : "Runs"} {day(campaign.startsAt)} to {day(campaign.endsAt)}</p>
        <NoteFor note={note} scope={campaign.id} />
        {campaign.approval === "Pending" && rival && (
          <Notice>“{rival.objective}” is already approved. Reject this plan unless you want to run both.</Notice>
        )}
        {campaign.lowConfidenceNotice && <Notice>{campaign.lowConfidenceNotice}</Notice>}
        <div className="note-ai">
          <AiMark />
          <p>{campaign.rationale}</p>
        </div>
        <ul className="featured" aria-label="Featured products">
          {campaign.products.map((product) => (
            <li key={product.id}>
              {product.name}
              {product.indicativePrice && ` · from $${product.indicativePrice}`}
            </li>
          ))}
        </ul>
        <div className="actions campaign-actions">
          {campaign.approval === "Pending" && (
            <>
              <button className="primary" disabled={busy}
                onClick={() => act(campaign.id, () => api.decideCampaign(campaign, "approve", session.csrfToken), `Version ${campaign.version} is approved. You can write posts for it now.`)}>
                Approve version {campaign.version}
              </button>
              <button className="secondary" disabled={busy}
                onClick={() => act("page", () => api.decideCampaign(campaign, "reject", session.csrfToken), `“${campaign.objective}” is rejected and moved to Earlier plans.`)}>
                Reject
              </button>
            </>
          )}
          {campaign.approval !== "Pending" && !over && (
            <button className="secondary ai" disabled={busy}
              onClick={() => act(campaign.id, () => api.reviseCampaign(campaign, plan.start, plan.end, session.csrfToken), `Version ${campaign.version + 1} is ready for your review. Its posts wait until you approve it.`)}>
              <AiMark />
              Plan again as a new version
            </button>
          )}
        </div>
        {campaign.approval === "Approved" && !over && (
          <form className="post-form" onSubmit={write}>
            <div className="actions">
              <label>
                Platform
                <select value={platform} disabled={busy} onChange={(e) => setPlatform(e.target.value as PostPlan["platform"])}>
                  <option value="Instagram">Instagram</option>
                  <option value="Facebook">Facebook</option>
                </select>
              </label>
              <label>
                Post time
                <input type="datetime-local" required value={time} disabled={busy} onChange={(e) => setTime(e.target.value)} />
              </label>
              <button className="primary ai" type="submit" disabled={busy || !time}>
                <AiMark />
                Write a post
              </button>
            </div>
          </form>
        )}
        {live.map(post)}
        {older.length > 0 && (
          <details className="earlier">
            <summary>
              Earlier posts <span className="count">{older.length}</span>
            </summary>
            <p className="field-help">Rejected posts and posts written for an older version of this plan. None of them goes out unless you write it again.</p>
            {older.map(post)}
          </details>
        )}
      </div>
    </section>
  );
}

export function MarketingPage({ session }: { session: Session }) {
  const [campaigns, setCampaigns] = useState<MarketingCampaign[] | null>(null);
  const [feed, setFeed] = useState<PublishedPost[]>([]);
  const [summary, setSummary] = useState<MarketingSummary | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState(false);
  const [start, setStart] = useState(() => daysFromNow(1));
  const [end, setEnd] = useState(() => daysFromNow(7));
  const validDates = start !== "" && end !== "" && end >= start;
  // New plans wait side by side to be compared; a revised plan stays with its posts.
  // Rejected plans and finished campaigns fold away but stay on record.
  const fresh = campaigns?.filter((c) => c.approval === "Pending" && c.posts.length === 0) ?? [];
  const earlier = campaigns?.filter((c) => c.approval === "Rejected" || c.approval === "Withdrawn" || (c.approval === "Approved" && ended(c))) ?? [];
  const current = campaigns?.filter((c) => !fresh.includes(c) && !earlier.includes(c)) ?? [];
  const rival = current.find((c) => c.approval === "Approved");

  const load = useCallback(
    () =>
      Promise.all([api.marketingCampaigns(), api.marketingFeed()]).then(
        ([plans, posts]) => {
          setError("");
          setCampaigns(plans);
          setFeed(posts);
        },
        (err: unknown) => setError(err instanceof ApiError ? err.message : "Marketing could not be loaded."),
      ),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!validDates) {
      setSummary(null);
      return;
    }
    let active = true;
    api.marketingSummary(start, end).then(
      (figures) => { if (active) setSummary(figures); },
      () => { if (active) setSummary(null); },
    );
    return () => {
      active = false;
    };
  }, [start, end, validDates]);

  const act: Act = async (scope, action, done) => {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      await action();
      setNote({ scope, text: typeof done === "string" ? done : done(), error: false });
      await load();
    } catch (err) {
      setNote({ scope, text: err instanceof ApiError ? err.message : "Nothing was changed. Please try again.", error: true });
      await load();
    } finally {
      setBusy(false);
    }
  };

  function plan(event?: FormEvent) {
    event?.preventDefault();
    if (busy || !validDates) return;
    act("page", () => api.planCampaign(start, end, session.csrfToken), fresh.length
      ? "Another plan is ready. Compare them, approve one and reject the others."
      : "The plan is ready and waiting for your approval.");
  }

  const card = (campaign: MarketingCampaign, against?: MarketingCampaign) => (
    <CampaignCard key={`${campaign.id}-${campaign.version}`} campaign={campaign} session={session} busy={busy}
      act={act} note={note} rival={against} />
  );

  function publishDue() {
    let result = { published: 0, failed: [] as string[] };
    act("feed", async () => {
      result = await api.publishDue(session.csrfToken);
    }, () => {
      const posted = result.published === 1 ? "1 post" : `${result.published} posts`;
      return result.published === 0 && result.failed.length === 0
        ? "Nothing is due: a post goes out once it is approved, in the calendar, allowed to publish and its time has come."
        : `Published ${posted}.${result.failed.length ? ` ${result.failed.length} failed: ${result.failed[0]}` : ""}`;
    });
  }

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Marketing</h1>
        <p>Plan a campaign, approve every post, then publish it.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
      <NoteFor note={note} scope="page" />
      {!campaigns && !error && <Notice>Loading marketing…</Notice>}
      {fresh.length > 0 && (
        <section className="compare" aria-labelledby="compare-title">
          <div className="section-heading">
            <h2 id="compare-title">{fresh.length === 1 ? "A plan waiting for you" : `${fresh.length} plans to compare`}</h2>
            <button className="secondary ai" disabled={busy || !validDates} onClick={() => plan()}>
              <AiMark />
              Ask for another plan
            </button>
          </div>
          <p className="field-help">Approve the plan you want and reject the others. Another plan uses the dates under “Plan a campaign”.</p>
          <div className="compare-grid">{fresh.map((campaign) => card(campaign, rival))}</div>
        </section>
      )}
      <div className="plan-grid marketing-grid">
        <div>
          {campaigns && current.length === 0 && (
            <section className="card">
              <div className="empty">
                {campaigns.length === 0 ? (
                  <>
                    <h3>No campaigns yet</h3>
                    <p>Choose the dates and ask the assistant for a plan. Ask more than once to compare plans; nothing happens until you approve one.</p>
                  </>
                ) : fresh.length > 0 ? (
                  <>
                    <h3>Nothing approved yet</h3>
                    <p>Approve one of the plans above to start writing its posts.</p>
                  </>
                ) : (
                  <>
                    <h3>No campaign running</h3>
                    <p>Ask for a plan to start a new one.</p>
                  </>
                )}
              </div>
            </section>
          )}
          {current.map((campaign) => card(campaign))}
          <section className="card orders" aria-labelledby="feed-title">
            <div className="section-heading">
              <h2 id="feed-title">Published</h2>
              <button className="secondary" disabled={busy} onClick={publishDue}>Publish what’s due</button>
            </div>
            <p className="field-help">A local mock channel for the demo: no real account is used and nothing leaves this computer.</p>
            <NoteFor note={note} scope="feed" />
            {feed.length === 0 ? (
              <div className="empty">
                <h3>Nothing published yet</h3>
              </div>
            ) : (
              <ul className="feed">
                {feed.map((post) => (
                  <li key={`${post.reference}-${post.publishedAt}`}>
                    <span className="caps">{post.platform} · {when(post.publishedAt)}</span>
                    <p>{post.body}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {earlier.length > 0 && (
            <details className="earlier">
              <summary>
                Earlier plans <span className="count">{earlier.length}</span>
              </summary>
              <p className="field-help">Rejected plans and finished campaigns stay on record.</p>
              {earlier.map((campaign) => card(campaign))}
            </details>
          )}
        </div>
        <form className="card plan-form" onSubmit={plan}>
          <h2>Plan a campaign</h2>
          <p className="field-help">The assistant suggests products and dates from your records. Ask more than once to compare plans; nothing happens until you approve one.</p>
          <div className="dates">
            <label>
              First day
              <input type="date" value={start} disabled={busy} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label>
              Last day
              <input type="date" value={end} min={start} disabled={busy} aria-invalid={!validDates}
                onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>
          <button className="primary ai" type="submit" disabled={busy || !validDates}>
            <AiMark />
            {busy ? "Working…" : "Ask for a plan"}
          </button>
          {summary && (
            <div className="plan-facts">
              <h3>What the plan will use</h3>
              {summary.topProducts.length > 0 ? (
                <ul aria-label="Best sellers">
                  {summary.topProducts.slice(0, 3).map((product) => (
                    <li key={product.id}>{product.name}: {product.unitsSold} sold</li>
                  ))}
                </ul>
              ) : (
                <p>No sales in the last 90 days.</p>
              )}
              <p>Profit over the last 90 days: ${summary.overall.profit}</p>
              <p>Free production time in these dates: {hours(summary.freeMinutes)}</p>
              {!summary.sufficient && <p>There isn’t much history yet, so the plan will be a general suggestion.</p>}
            </div>
          )}
        </form>
      </div>
    </>
  );
}
