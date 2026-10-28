import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type PlanResult,
  type ReleaseResult,
  type Schedule,
  type Schedules,
  type ScheduleVersion,
  type Session,
} from "../api/client";
import { Notice } from "../components/Notice";

// UC-04 (BAKE-20, BAKE-21): the owner asks for a production plan, reviews its
// current version and approves it for release. Every check runs on the
// server; task times are shown exactly as given, in the bakery's time zone.

const STATES: Record<Schedule["state"], string> = {
  Draft: "Draft",
  AwaitingApproval: "Awaiting your approval",
  NeedsRevision: "Needs revision",
  Approved: "Approved · released",
};
const TASK_STATUS: Record<string, string> = { InProgress: "In progress" };

const short = (id: string) => id.slice(0, 8);
const clock = (iso: string) => iso.slice(11, 16);
const days = (v: ScheduleVersion) => (v.startDate === v.endDate ? v.startDate : `${v.startDate} to ${v.endDate}`);
const today = () => new Date().toLocaleDateString("en-CA");
const failure = (err: unknown) => (err instanceof ApiError ? err.message : "The production plans could not be loaded.");

type Message = { error: boolean; text: string; items: string[] };

function planMessage(result: PlanResult): Message {
  const excluded = result.excluded.map((e) => `Order ${short(e.orderId)} was left out: ${e.reason}`);
  if (result.status === "Proposed")
    return { error: false, text: `Version ${result.schedule?.version} is ready for your review.`, items: excluded };
  if (result.status === "NoCandidates")
    return { error: false, text: "No confirmed orders can be planned for these days.", items: excluded };
  return {
    error: true,
    text: "The planning assistant gave no usable plan. Nothing was saved.",
    items: [...result.problems, ...excluded],
  };
}

function releaseMessage(result: ReleaseResult, version: number): Message {
  if (result.status === "Released")
    return { error: false, text: `Version ${version} is approved and released for production.`, items: [] };
  if (result.status === "Withheld")
    return {
      error: false,
      text: `You withheld approval of version ${version}. It stays unapproved until you approve it or request a revised plan.`,
      items: [],
    };
  return {
    error: true,
    text: `Version ${version} could not be released. Your approval is recorded, but nothing was released:`,
    items: result.problems,
  };
}

function Tasks({ caption, version }: { caption: string; version: ScheduleVersion }) {
  return (
    <div className="table-scroll">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{caption}</th>
            <th scope="col">Time</th>
            <th scope="col">Order</th>
            <th scope="col">Task</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {version.tasks.map((t) => (
            <tr key={t.id}>
              <th scope="row">{t.startAt.slice(0, 10)}</th>
              <td>
                {clock(t.startAt)}–{clock(t.endAt)}
              </td>
              <td>Order {short(t.orderId)}</td>
              <td>{t.kind}</td>
              <td>{TASK_STATUS[t.status] ?? t.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SchedulesPage({ session }: { session: Session }) {
  const [data, setData] = useState<Schedules | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState<Message | null>(null);
  const [busy, setBusy] = useState(false);
  const [startDate, setStartDate] = useState(today);
  const [endDate, setEndDate] = useState(today);

  const load = useCallback(() => api.schedules().then(setData, (err: unknown) => setError(failure(err))), []);
  useEffect(() => {
    load();
  }, [load]);

  async function run(action: () => Promise<Message>) {
    setBusy(true);
    setMessage(null);
    setError("");
    try {
      setMessage(await action());
      await load();
    } catch (err) {
      setError(failure(err));
    } finally {
      setBusy(false);
    }
  }
  const plan = (start: string, end: string, scheduleId?: string) =>
    run(async () => planMessage(await api.planSchedule(start, end, session.csrfToken, scheduleId)));
  const decide = (s: Schedule, decision: "Approved" | "Withheld") =>
    run(async () => releaseMessage(await api.decideSchedule(s.id, s.version, decision, session.csrfToken), s.version));
  const datesValid = Boolean(startDate && endDate && endDate >= startDate);

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Schedules</h1>
        <p>Make room for a well-planned baking day.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
      {message && (
        <Notice error={message.error}>
          {message.text}
          {message.items.length > 0 && (
            <ul>
              {message.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </Notice>
      )}
      <section className="card plan-form" aria-labelledby="plan-title">
        <h2 id="plan-title">Plan production</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (datesValid) plan(startDate, endDate);
          }}
        >
          <div className="actions">
            <label>
              First day
              <input type="date" value={startDate} required disabled={busy} onChange={(e) => setStartDate(e.target.value)} />
            </label>
            <label>
              Last day
              <input type="date" value={endDate} required disabled={busy} onChange={(e) => setEndDate(e.target.value)} />
            </label>
          </div>
          <p className="field-help">
            Confirmed orders due on these days are planned. Nothing is released for production until you approve it.
          </p>
          <button className="primary" type="submit" disabled={busy || !datesValid}>
            {busy ? "Working…" : "Ask for a plan"}
          </button>
        </form>
      </section>
      {!data && !error && <Notice>Loading production plans…</Notice>}
      {data && data.schedules.length === 0 && (
        <section className="card schedule">
          <div className="empty">
            <h3>No plans yet</h3>
            <p>Plans you ask for will appear here for your review.</p>
          </div>
        </section>
      )}
      {data?.schedules.map((s) => (
        <section className="card schedule" key={s.id} aria-labelledby={`plan-${s.id}`}>
          <div className="section-heading">
            <h2 id={`plan-${s.id}`}>
              Plan {short(s.id)} · {days(s.latest)}
            </h2>
            <span className="badge">{STATES[s.state]}</span>
          </div>
          <p className="field-help">
            Version {s.version}
            {s.released && ` · version ${s.released.version} stays in use until this one is released`}. Times in{" "}
            {data.timeZone}.
          </p>
          {s.notice && <div className="notice">{s.notice}</div>}
          <Tasks caption={`Version ${s.version}`} version={s.latest} />
          {s.released && <Tasks caption={`Released version ${s.released.version}`} version={s.released} />}
          <div className="actions">
            {s.state === "AwaitingApproval" && (
              <>
                <button className="primary" disabled={busy} onClick={() => decide(s, "Approved")}>
                  Approve and release version {s.version}
                </button>
                <button className="secondary" disabled={busy} onClick={() => decide(s, "Withheld")}>
                  Withhold approval
                </button>
              </>
            )}
            <button
              className="secondary"
              disabled={busy}
              onClick={() => plan(s.latest.startDate, s.latest.endDate, s.id)}
            >
              Request a revised plan
            </button>
          </div>
        </section>
      ))}
    </>
  );
}
