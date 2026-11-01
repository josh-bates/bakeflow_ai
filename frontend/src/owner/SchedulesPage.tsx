import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type PlanResult,
  type ReleaseResult,
  type Schedule,
  type Schedules,
  type ScheduleVersion,
  type Session,
  type TaskProgress,
} from "../api/client";
import { AiMark } from "../components/AiMark";
import { readableAmount } from "../format";
import { Notice } from "../components/Notice";

// UC-04 (BAKE-20, BAKE-21): the owner asks for a production plan, reviews its
// current version and approves it for release. Every check runs on the
// server; task times are shown exactly as given, in the bakery's time zone.
// IP-01 (BAKE-33): the owner records progress on the released version's
// tasks; finishing an order's last task deducts its stock once.

const STATES: Record<Schedule["state"], string> = {
  Draft: "Draft",
  AwaitingApproval: "Awaiting your approval",
  NeedsRevision: "Needs revision",
  Approved: "Approved · released",
};
const LIDS: Record<Schedule["state"], { tone: string; label: string }> = {
  Draft: { tone: "", label: "Draft plan" },
  AwaitingApproval: { tone: "blush", label: "Needs your decision" },
  NeedsRevision: { tone: "apricot", label: "Needs a revised plan" },
  Approved: { tone: "mint", label: "In the kitchen" },
};
const STEPS = ["Ask for a plan", "Check the tasks", "Approve and release"];
const TASK_STATUS: Record<string, string> = { InProgress: "In progress" };

const short = (id: string) => id.slice(0, 8);
const clock = (iso: string) => iso.slice(11, 16);
const today = () => new Date().toLocaleDateString("en-CA");
const failure = (err: unknown) => (err instanceof ApiError ? err.message : "The production plans could not be loaded.");

type Message = { error: boolean; text: string; items: string[] };
type Task = ScheduleVersion["tasks"][number];
type Progress = TaskProgress["status"];

function planMessage(result: PlanResult): Message {
  const excluded = result.excluded.map((e) => `Order ${short(e.orderId)} was left out: ${e.reason}`);
  if (result.status === "Proposed")
    return { error: false, text: `Version ${result.schedule?.version} is ready for your review.`, items: excluded };
  // Orders were due but each one was left out (stock, another plan, a pending change): say that, not "wrong days".
  if (result.status === "NoCandidates" && excluded.length)
    return {
      error: true,
      text: `Nothing was planned: ${excluded.length === 1 ? "the order due on these days was" : `all ${excluded.length} orders due on these days were`} left out. Fix the reason below, then ask again.`,
      items: excluded,
    };
  if (result.status === "NoCandidates")
    return {
      error: false,
      text: "No confirmed orders are due on these days, so nothing was planned. A plan covers the orders collected on its days; to bake earlier, choose days from the baking day up to the collection day.",
      items: [],
    };
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
      text: `You haven't approved version ${version} yet. It stays unreleased until you approve it or request a revised plan.`,
      items: [],
    };
  return {
    error: true,
    text: `Version ${version} could not be released. Your approval is recorded, but nothing was released:`,
    items: result.problems,
  };
}

function progressMessage(result: TaskProgress, task: Task): Message {
  const order = short(task.orderId);
  const d = result.deduction;
  if (d)
    return {
      error: false,
      text: result.replayed
        ? `Order ${order} was already complete. Nothing was deducted again:`
        : `Order ${order} is complete. Its ingredients were deducted once:`,
      items: d.lines.map((l) => `${l.ingredient}: ${readableAmount(l.quantity, l.unit)} used, ${readableAmount(l.onHand, l.unit)} on hand now`),
    };
  const done = result.status === "Completed" ? "is done" : "has started";
  return { error: false, text: `The ${task.kind} task for order ${order} ${done}.`, items: [] };
}

// Dates are the server's calendar days, so they are formatted without a time zone shift.
const day = (date: string, options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-AU", { ...options, timeZone: "UTC" }).format(new Date(`${date}T00:00:00Z`));

function PlanDays({ version: v }: { version: ScheduleVersion }) {
  const span: Intl.DateTimeFormatOptions = { day: "numeric", month: "long" };
  const year = day(v.endDate, { year: "numeric" });
  if (v.startDate !== v.endDate)
    return (
      <>
        <em>{day(v.startDate, span)}</em> to <em>{day(v.endDate, span)}</em> {year}
      </>
    );
  return (
    <>
      {day(v.startDate, { weekday: "long" })} <em>{day(v.startDate, span)}</em> {year}
    </>
  );
}

function duration(t: Task) {
  const minutes = Math.round((Date.parse(t.endAt) - Date.parse(t.startAt)) / 60000);
  const hours = Math.floor(minutes / 60);
  return [hours > 0 && `${hours} h`, minutes % 60 > 0 && `${minutes % 60} min`].filter(Boolean).join(" ");
}

function taskCount(v: ScheduleVersion) {
  const tasks = v.tasks.length;
  const orders = new Set(v.tasks.map((t) => t.orderId)).size;
  return `${tasks} ${tasks === 1 ? "task" : "tasks"} for ${orders} ${orders === 1 ? "order" : "orders"}`;
}

function Tasks({
  caption,
  version,
  busy = false,
  onProgress,
}: {
  caption: string;
  version: ScheduleVersion;
  busy?: boolean;
  // Only the released version's tasks take progress.
  onProgress?: (task: Task, status: Progress) => void;
}) {
  const severalDays = version.startDate !== version.endDate;
  return (
    <div className="table-scroll">
      <table className="tasks">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Task</th>
            <th scope="col">Status</th>
            {onProgress && <th scope="col">Progress</th>}
          </tr>
        </thead>
        <tbody>
          {version.tasks.map((t) => (
            <tr key={t.id} data-kind={t.kind}>
              <th scope="row">
                {clock(t.startAt)}–{clock(t.endAt)}
                {severalDays && (
                  <span className="task-day">{day(t.startAt.slice(0, 10), { weekday: "short", day: "numeric", month: "short" })}</span>
                )}
              </th>
              <td>
                <span className="task-name">{t.kind}</span>
                <span className="task-span">
                  {duration(t)} · Order {short(t.orderId)}
                </span>
              </td>
              <td>
                <span className="badge" data-status={t.status}>{TASK_STATUS[t.status] ?? t.status}</span>
              </td>
              {onProgress && (
                <td>
                  <div className="actions">
                    {(t.status === "Planned" || t.status === "Blocked") && (
                      <button
                        className="secondary"
                        disabled={busy}
                        aria-label={`Start the ${t.kind} task for order ${short(t.orderId)}`}
                        onClick={() => onProgress(t, "InProgress")}
                      >
                        Start
                      </button>
                    )}
                    {t.status !== "Completed" && (
                      <button
                        className="secondary"
                        disabled={busy}
                        aria-label={`Mark the ${t.kind} task for order ${short(t.orderId)} done`}
                        onClick={() => onProgress(t, "Completed")}
                      >
                        Mark done
                      </button>
                    )}
                  </div>
                </td>
              )}
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
  // Days the owner chose in the form; a revised plan can move to them.
  const [picked, setPicked] = useState(false);
  // The answer appears above the plans; bring it into view, since the button may be far below.
  const answer = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (message || error) answer.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [message, error]);

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
  const progress = (s: Schedule) => (t: Task, status: Progress) =>
    run(async () => progressMessage(await api.recordTaskProgress(s.id, t.id, status, session.csrfToken), t));
  const datesValid = Boolean(startDate && endDate && endDate >= startDate);
  // A plan holds its orders, so other days are reached by revising it, not by a second plan.
  const movesTo = (s: Schedule) =>
    picked && datesValid && (startDate !== s.latest.startDate || endDate !== s.latest.endDate);
  const waiting = Boolean(data?.schedules.some((s) => s.state === "AwaitingApproval"));
  const released = Boolean(data?.schedules.some((s) => s.state === "Approved"));
  // One step at a time: a plan waiting for a decision comes before any released one.
  const step = waiting ? 2 : released ? 4 : 1;

  return (
    <>
      <section className="intro owner-intro">
        <p className="eyebrow">Inventory & production</p>
        <h1>Production <em>schedules</em></h1>
        <ol className="method" aria-label="How a plan is released">
          {STEPS.map((label, index) => {
            const number = index + 1;
            const state = number < step ? "done" : number === step ? "now" : undefined;
            return (
              <li key={label} className={state} aria-current={state === "now" ? "step" : undefined}>
                <span className="dot" aria-hidden="true">{state === "done" ? "✓" : number}</span>
                {label}
              </li>
            );
          })}
        </ol>
      </section>
      <div ref={answer}>
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
      </div>
      <div className="plan-grid">
        <div className="plan-list">
          {!data && !error && <Notice>Loading production plans…</Notice>}
          {data && data.schedules.length === 0 && (
            <section className="card">
              <div className="empty">
                <h3>No plans yet</h3>
                <p>Plans you ask for will appear here for your review.</p>
              </div>
            </section>
          )}
          {data?.schedules.map((s) => (
            <section className="card schedule" key={s.id} aria-labelledby={`plan-${s.id}`}>
              <div className={`lid ${LIDS[s.state].tone}`}>
                <span className="caps">{LIDS[s.state].label}</span>
                <span className="badge" data-status={s.state}>{STATES[s.state]}</span>
              </div>
              <div className="plan-body">
                <div className="plan-top">
                  <span className="caps">Production plan · Version {s.version}</span>
                  <button
                    className="text-button ai"
                    disabled={busy}
                    onClick={() => (movesTo(s) ? plan(startDate, endDate, s.id) : plan(s.latest.startDate, s.latest.endDate, s.id))}
                  >
                    <AiMark />
                    {movesTo(s)
                      ? `Re-plan for ${day(startDate, { day: "numeric", month: "short" })}${startDate === endDate ? "" : ` to ${day(endDate, { day: "numeric", month: "short" })}`}`
                      : "Request a revised plan"}
                  </button>
                </div>
                <h2 id={`plan-${s.id}`}>
                  <PlanDays version={s.latest} />
                </h2>
                <p className="field-help">
                  {taskCount(s.latest)}
                  {s.released && ` · version ${s.released.version} stays in use until this one is released`}. Times in{" "}
                  {data.timeZone}.
                </p>
                {s.notice && <div className="notice">{s.notice}</div>}
                <Tasks
                  caption={`Version ${s.version}`}
                  version={s.latest}
                  busy={busy}
                  onProgress={s.releasedVersion === s.version ? progress(s) : undefined}
                />
                {s.released && (
                  <>
                    <p className="caps released-title">In the kitchen · Version {s.released.version}</p>
                    <Tasks
                      caption={`Released version ${s.released.version}`}
                      version={s.released}
                      busy={busy}
                      onProgress={progress(s)}
                    />
                  </>
                )}
                {s.state === "AwaitingApproval" && (
                  <div className="decision">
                    <p>Approving sends these tasks to the kitchen and sets aside their ingredients.</p>
                    <div className="actions">
                      <button className="secondary" disabled={busy} onClick={() => decide(s, "Withheld")}>
                        Not yet
                      </button>
                      <button className="primary" disabled={busy} onClick={() => decide(s, "Approved")}>
                        Approve and release version {s.version} <span className="arrow" aria-hidden="true">→</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </section>
          ))}
        </div>
        <section className="card plan-form" aria-labelledby="plan-title">
          <h2 id="plan-title">
            Ask for a <em>plan</em>
          </h2>
          <p className="field-help">
            Confirmed orders due on these days are planned. Nothing is released for production until you approve it.
          </p>
          {data?.schedules.length ? (
            <p className="field-help">
              To move an existing plan to other days, choose them here and use “Re-plan for …” on that plan.
            </p>
          ) : null}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (datesValid) plan(startDate, endDate);
            }}
          >
            <div className="dates">
              <label>
                First day
                <input type="date" value={startDate} required disabled={busy} onChange={(e) => { setStartDate(e.target.value); setPicked(true); }} />
              </label>
              <label>
                Last day
                <input type="date" value={endDate} required disabled={busy} onChange={(e) => { setEndDate(e.target.value); setPicked(true); }} />
              </label>
            </div>
            {/* A plan waiting for a decision keeps the page's one primary button. */}
            <button className={waiting ? "secondary ai" : "primary"} type="submit" disabled={busy || !datesValid}>
              <AiMark />
              {busy ? "Working…" : "Ask for a plan"}
            </button>
          </form>
        </section>
      </div>
    </>
  );
}
