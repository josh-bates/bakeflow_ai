import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type ExtraCost,
  type OwnerQuote,
  type QuoteQueueItem,
  type Session,
} from "../api/client";
import { Notice } from "../components/Notice";

// UC-05 (BAKE-23): the owner prices an order, reviews the calculated quote
// and approves exactly one version for the customer. Every figure comes from
// the server as a decimal string and is shown as given.

const short = (id: string) => id.slice(0, 8);
const money = (value: string) => `$${value}`;
const percent = (margin: string) => `${(Math.round(Number(margin) * 1000) / 10).toFixed(1)}%`;
const when = (iso: string) =>
  new Intl.DateTimeFormat("en-AU", { dateStyle: "medium", timeStyle: "short", timeZone: "Australia/Sydney" }).format(
    new Date(iso),
  );
// What the server accepts exactly: a positive amount with at most two decimals.
const AMOUNT = /^\d{1,9}(\.\d{1,2})?$/;

const APPROVAL: Record<string, string> = {
  Pending: "Awaiting your approval",
  Approved: "Approved · shown to the customer",
  Withheld: "Withheld · revise it",
};
const DECISION: Record<string, string> = {
  Pending: "Customer has not decided",
  Accepted: "Customer accepted",
  Rejected: "Customer rejected · revise it",
};

type Draft = { description: string; amount: string };

function Breakdown({ quote }: { quote: OwnerQuote }) {
  const rows: [string, string][] = [
    ["Ingredients", quote.materialCost],
    ["Labour", quote.labourCost],
    ["Packaging", quote.packagingCost],
    ["Additional costs", quote.additionalCost],
  ];
  return (
    <div className="table-scroll">
      <table>
        <caption className="sr-only">Quote version {quote.version} breakdown</caption>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{money(value)}</td>
            </tr>
          ))}
          <tr>
            <th scope="row">Quoted price (target margin {percent(quote.targetMargin)})</th>
            <td>
              <strong>{money(quote.total)}</strong>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function ApprovalsPage({ session }: { session: Session }) {
  const [items, setItems] = useState<QuoteQueueItem[] | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [extras, setExtras] = useState<Record<string, Draft>>({});

  const load = useCallback(
    () =>
      api.quoteQueue().then(setItems, (err: unknown) =>
        setError(err instanceof ApiError ? err.message : "The quotes could not be loaded."),
      ),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  const extra = (orderId: string): Draft => extras[orderId] ?? { description: "", amount: "" };
  const setExtra = (orderId: string, change: Partial<Draft>) =>
    setExtras((current) => ({ ...current, [orderId]: { ...extra(orderId), ...change } }));
  const costs = (orderId: string): ExtraCost[] | null => {
    const { description, amount } = extra(orderId);
    if (!description.trim() && !amount) return [];
    if (!description.trim() || !AMOUNT.test(amount)) return null;
    return [{ description: description.trim(), amount }];
  };

  async function act(orderId: string, action: () => Promise<OwnerQuote>, done: (q: OwnerQuote) => string) {
    if (busy) return;
    setBusy(orderId);
    setMessage("");
    setError("");
    try {
      const quote = await action();
      setMessage(done(quote));
      setExtras((current) => ({ ...current, [orderId]: { description: "", amount: "" } }));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Nothing was changed. Please try again.");
    } finally {
      setBusy("");
    }
  }

  function price(item: QuoteQueueItem) {
    const added = costs(item.orderId);
    if (added === null) return;
    const label = `Order ${short(item.orderId)}`;
    if (item.quote) {
      const quote = item.quote;
      act(item.orderId, () => api.reviseQuote(quote, added, session.csrfToken),
        (q) => `${label}: version ${q.version} is ready for your review.`);
    } else {
      act(item.orderId, () => api.draftQuote(item.orderId, added, session.csrfToken),
        (q) => `${label}: version ${q.version} is ready for your review.`);
    }
  }

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Pending approvals</h1>
        <p>Review the decisions that need your attention.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
      {message && <Notice>{message}</Notice>}
      {!items && !error && <Notice>Loading quotes…</Notice>}
      {items && items.length === 0 && (
        <section className="card orders">
          <div className="empty">
            <h3>Nothing to quote</h3>
            <p>New customer orders appear here to be priced. Production plans are approved on the Schedules page.</p>
          </div>
        </section>
      )}
      {items?.map((item) => {
        const quote = item.quote;
        const label = `Order ${short(item.orderId)}`;
        const draft = extra(item.orderId);
        const invalidExtra = costs(item.orderId) === null;
        const accepted = quote?.customerDecision === "Accepted";
        const working = busy === item.orderId;
        return (
          <section key={item.orderId} className="card orders" aria-labelledby={`quote-${item.orderId}`}>
            <h2 id={`quote-${item.orderId}`}>
              {label} · {item.summary}
            </h2>
            <p className="field-help">
              Collection {when(item.requiredAt)} (Australia/Sydney) · <span className="badge">{item.orderStatus}</span>
            </p>
            {!quote && !item.ready && (
              <Notice>The stock check for this order is not up to date yet, so it cannot be priced.</Notice>
            )}
            {quote && (
              <>
                <h3>
                  Version {quote.version} · {APPROVAL[item.approval ?? "Pending"]}
                </h3>
                <Breakdown quote={quote} />
                {quote.requiresManualReview && (
                  <Notice error>Flagged for manual review: check the figures before approving.</Notice>
                )}
                <p>
                  {quote.explanation ?? "Explanation unavailable. Please review the figures yourself."}
                </p>
                {item.approval === "Approved" && <p className="field-help">{DECISION[quote.customerDecision]}</p>}
                {item.approval === "Pending" && (
                  <div className="actions">
                    <button className="primary" disabled={!!busy}
                      onClick={() => act(item.orderId, () => api.decideQuote(quote, "approve", session.csrfToken),
                        (q) => `${label}: version ${q.version} is approved and shown to the customer.`)}>
                      Approve version {quote.version}
                    </button>
                    <button className="secondary" disabled={!!busy}
                      onClick={() => act(item.orderId, () => api.decideQuote(quote, "withhold", session.csrfToken),
                        (q) => `${label}: version ${q.version} is withheld. Revise it to send a new quote.`)}>
                      Withhold and revise
                    </button>
                  </div>
                )}
              </>
            )}
            {(quote ? !accepted : item.ready) && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  price(item);
                }}
              >
                <div className="actions">
                  <label>
                    Extra cost (optional)
                    <input value={draft.description} maxLength={120} disabled={working} placeholder="Gift box"
                      onChange={(e) => setExtra(item.orderId, { description: e.target.value })} />
                  </label>
                  <label>
                    Amount (AUD)
                    <input value={draft.amount} inputMode="decimal" autoComplete="off" disabled={working}
                      aria-invalid={invalidExtra}
                      onChange={(e) => setExtra(item.orderId, { amount: e.target.value.trim() })} />
                  </label>
                </div>
                {invalidExtra && (
                  <p className="field-help">Give the extra cost a name and an amount such as 12.50.</p>
                )}
                <button className={quote ? "secondary" : "primary"} type="submit" disabled={!!busy || invalidExtra}>
                  {working ? "Calculating…" : quote ? "Revise as a new version" : "Calculate quote"}
                </button>
              </form>
            )}
          </section>
        );
      })}
    </>
  );
}
