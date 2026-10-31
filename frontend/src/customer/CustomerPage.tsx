import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type Order,
  type Tracking,
  type Session,
  type Submission,
  type DraftReceipt,
  type CustomerQuote,
  type Payment,
} from "../api/client";
import { Notice } from "../components/Notice";

const EXAMPLE = "24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, no allergy concerns";
// Saved orders have long record ids; the first part is enough to tell them apart in the table.
const shortId = (id: string) => (/^[0-9a-f]{8}-[0-9a-f-]+$/i.test(id) ? id.slice(0, 8) : id);
// An order confirmed from an accepted quote can be paid until it is collected.
const PAYABLE = ["Confirmed", "Scheduled", "InProduction", "Completed"];

export function CustomerPage({
  session,
  onExpired,
}: {
  session: Session;
  onExpired: () => void;
}) {
  const [tracking, setTracking] = useState<Tracking | null>(null);
  const [trackingError, setTrackingError] = useState("");
  const [trackingBusy, setTrackingBusy] = useState(false);
  const trackRequest = useRef(0);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [message, setMessage] = useState("");
  const [answers, setAnswers] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const [ended, setEnded] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Submission | null>(null);
  const [saved, setSaved] = useState<DraftReceipt | null>(null);
  const pending = useRef(false);
  const [quote, setQuote] = useState<CustomerQuote | null>(null);
  const [quoteOrder, setQuoteOrder] = useState("");
  const [quoteBusy, setQuoteBusy] = useState(false);
  const [quoteError, setQuoteError] = useState("");
  const [quoteMessage, setQuoteMessage] = useState("");
  const [payOrder, setPayOrder] = useState("");
  const [payment, setPayment] = useState<Payment | null>(null);
  const [payBusy, setPayBusy] = useState(false);
  const [payError, setPayError] = useState("");
  const [payMessage, setPayMessage] = useState("");
  const attempt = useRef<{ message: string; key: string } | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");
    api
      .orders()
      .then((data) => {
        if (active) setOrders(data);
      })
      .catch((e: Error) => {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401) onExpired();
        else setLoadError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reload, onExpired]);

  async function track(orderId: string) {
    const request = ++trackRequest.current;
    setTracking(null); setTrackingError(""); setTrackingBusy(true);
    try {
      const value = await api.tracking(orderId);
      if (request === trackRequest.current) {
        setTracking(value);
        setOrders(current => current.map(order => order.id === value.id ? { ...order, status: value.status, requiredAt: value.requiredAt } : order));
      }
    } catch (e) {
      if (request !== trackRequest.current) return;
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setTrackingError(e instanceof ApiError && e.status === 404 ? "Order not available for your account." : (e as Error).message);
    } finally {
      if (request === trackRequest.current) setTrackingBusy(false);
    }
  }

  function changeRequest(text: string) {
    setMessage(text);
    setAnswers([]);
    setAnswer("");
    setEnded(false);
    setResult(null);
    setSaved(null);
    setError("");
  }

  // UC-05 steps 7-9 (BAKE-23): the customer sees only the owner-approved,
  // current quote and accepts or rejects exactly that version.
  async function viewQuote(orderId: string) {
    setQuote(null); setQuoteOrder(orderId); setQuoteError(""); setQuoteMessage(""); setQuoteBusy(true);
    try {
      const value = await api.customerQuote(orderId);
      if (value) setQuote(value);
      else setQuoteMessage("The bakery is still preparing your quote. Check back soon.");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setQuoteError((e as Error).message);
    } finally {
      setQuoteBusy(false);
    }
  }

  async function decide(accept: boolean) {
    if (!quote || quoteBusy) return;
    setQuoteBusy(true); setQuoteError("");
    try {
      if (accept) {
        await api.acceptQuote(quote, session.csrfToken);
        setQuoteMessage(`You accepted the quote of $${quote.total}. Your order is confirmed.`);
      } else {
        await api.rejectQuote(quote, session.csrfToken);
        setQuoteMessage("You declined this quote. The bakery may send you a revised one.");
      }
      setQuote(null);
      setReload(value => value + 1);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setQuoteError(`${(e as Error).message} Your decision was not recorded.`);
    } finally {
      setQuoteBusy(false);
    }
  }

  // BAKE-39: a local mock payment for a confirmed order. The amount is the
  // accepted quote's total, set by the server; no card details are asked for.
  async function openPayment(orderId: string) {
    setPayOrder(orderId); setPayment(null); setPayError(""); setPayMessage(""); setPayBusy(true);
    try {
      const paid = (await api.payments(orderId)).find(p => p.paid);
      if (paid) setPayMessage(`Paid $${paid.amount}. Thank you!`);
      else setPayment(await api.startPayment(orderId, session.csrfToken));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setPayError((e as Error).message);
    } finally {
      setPayBusy(false);
    }
  }

  async function pay(outcome: "Succeeded" | "Failed") {
    if (!payment || payBusy) return;
    setPayBusy(true); setPayError("");
    try {
      const settled = await api.mockCheckout(payment.id, outcome, session.csrfToken);
      if (settled.paid) {
        setPayMessage(`Payment of $${settled.amount} received. Thank you!`);
        setPayment(null);
      } else {
        // A failed or declined payment changes nothing; the customer can try again.
        setPayMessage(`The payment did not go through${settled.reason ? `: ${settled.reason}` : ""}. Nothing was charged.`);
        setPayment(null);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setPayError(`${(e as Error).message} Nothing was charged.`);
    } finally {
      setPayBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (pending.current) return;
    const text = message.trim();
    if (!text) {
      setError("Please describe what you would like to order.");
      return;
    }
    pending.current = true;
    setSending(true);
    setError("");
    setResult(null);
    setSaved(null);
    setAnswers([]);
    setAnswer("");
    setEnded(false);
    // Reuse the key after an uncertain response so a retry cannot create a second intake.
    if (attempt.current?.message !== text)
      attempt.current = { message: text, key: crypto.randomUUID() };
    try {
      const response = await api.submit(
        text,
        session.csrfToken,
        attempt.current.key,
      );
      setResult(response);
      attempt.current = null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else
        setError(
          `${(e as Error).message} No draft was saved. Your text has been kept so you can try again.`,
        );
    } finally {
      pending.current = false;
      setSending(false);
    }
  }

  async function clarify(event: React.FormEvent) {
    event.preventDefault();
    if (pending.current || !answer.trim() || answers.length >= 8) return;
    pending.current = true;
    setSending(true);
    setError("");
    const next = [...answers, answer.trim()];
    try {
      const response = await api.submit(message.trim(), session.csrfToken, crypto.randomUUID(), next);
      setResult(response);
      setAnswers(next);
      setAnswer("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setError(`${(e as Error).message} No draft was saved. Your answer has been kept so you can retry.`);
    } finally {
      pending.current = false;
      setSending(false);
    }
  }

  async function confirm() {
    if (pending.current || !result?.confirmationToken || result.issues.length) return;
    pending.current = true;
    setSending(true);
    setError("");
    try {
      const receipt = await api.confirm(result.confirmationToken, session.csrfToken);
      setSaved(receipt);
      setResult(null);
      setMessage("");
      setReload(value => value + 1);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setError(`${(e as Error).message} Saving has not been confirmed. Retry confirmation to check the same order.`);
    } finally {
      pending.current = false;
      setSending(false);
    }
  }

  return (
    <>
      <section className="dessert-hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <p className="eyebrow">BAKEFLOW / THE JOY OF BAKING</p>
          <h1 id="hero-title">Thoughtfully baked.<br /><em>For every occasion.</em></h1>
          <p>Something freshly baked for everyday moments and special occasions. Tell us what you have in mind.</p>
          <div className="hero-actions">
            <button className="primary" onClick={() => { document.getElementById('request-title')?.scrollIntoView({ block: 'start' }); document.getElementById('order-request')?.focus({ preventScroll: true }); }}>Start a custom order <span aria-hidden="true">↗</span></button>
            <button className="text-button" onClick={() => { document.getElementById('orders-title')?.scrollIntoView({ block: 'start' }); document.getElementById('orders-title')?.focus({ preventScroll: true }); }}>View my orders <span aria-hidden="true">→</span></button>
          </div>
          <p className="hero-note">Everyday favourites. Special moments. Made for you.</p>
        </div>
        <figure className="hero-photo">
          <img src="/images/chocolate-cake.jpg" alt="Layered chocolate cake with red berries and cocoa on a white ceramic plate" width="1600" height="900" fetchPriority="high" />
          <figcaption><span>A MOMENT OF SWEETNESS</span><span>01 / Chocolate</span></figcaption>
        </figure>
      </section>
      <div className="customer-grid">
        <section className="card" aria-labelledby="request-title">
          <div className="request-intro">
          <div className="section-number">01 / MADE FOR YOU</div>
          <h2 id="request-title">Request a custom order</h2>
          <p className="muted">
            Include the product, quantity, flavours and collection date and time.
            State any allergy concerns, or explicitly say you have none.
          </p>
          </div>
          <form onSubmit={submit}>
            <label htmlFor="order-request">What would you like to order?</label>
            <textarea
              id="order-request"
              value={message}
              maxLength={4000}
              required
              disabled={sending}
              onChange={(e) => changeRequest(e.target.value)}
              placeholder={EXAMPLE}
              aria-describedby="request-help"
            />
            <p id="request-help" className="field-help">
              Review your summary before saving a draft. A draft is not a confirmed sale and no payment is taken.
            </p>
            {error && <Notice error>{error}</Notice>}
            {ended && <Notice>Request ended. No draft was saved.</Notice>}
            {saved && <Notice><strong>Draft saved · {saved.orderId}</strong><br />Your requirements are saved. This is not a confirmed sale.</Notice>}
            <button className="primary" type="submit" disabled={sending}>
              {sending ? "Sending request…" : "Send order request"}{" "}
              <span aria-hidden="true">↗</span>
            </button>
          </form>
          {session.mode === "mock" && !result && <p className="field-help demo-hint">Offline demo: mock mode supports preset examples only. <button type="button" className="text-button" disabled={sending} onClick={() => changeRequest(EXAMPLE)}>Use the example</button></p>}
          {result && (
            <section className="order-summary" aria-labelledby="summary-title" aria-live="polite">
              <p className="eyebrow">REVIEW YOUR REQUEST</p>
              <h3 id="summary-title">Your order summary</h3>
              <ul>{result.summary.items.map((item, index) => <li key={index}>
                <strong>{item.quantity ?? "Quantity needed"} × {item.product}</strong>
                {item.flavour && <p>Flavour: {item.flavour}</p>}
                {item.theme && <p>Theme: {item.theme}</p>}
                {item.customisation && <p>Customisation: {item.customisation}</p>}
              </li>)}</ul>
              <dl>
                <dt>Collection ({result.summary.collection_kind === "before" ? "before" : "at"})</dt>
                <dd>{result.summary.required_at ? new Intl.DateTimeFormat("en-AU", { dateStyle: "full", timeStyle: "short", timeZone: "Australia/Sydney" }).format(new Date(result.summary.required_at)) : "Not provided"} · Australia/Sydney</dd>
                <dt>Allergen concerns</dt><dd>{result.summary.allergen_concerns === null ? "Not provided" : result.summary.allergen_concerns.length ? result.summary.allergen_concerns.join(", ") : "No concerns stated by you"}</dd>
                <dt>Budget (AUD)</dt><dd>{result.summary.budget ?? "Not specified"}</dd>
              </dl>
              <p className="field-help">Check the full date and year. If you omitted a year, the next occurrence is shown. No draft has been saved yet.</p>
              {result.issues.length > 0 && <Notice error><ul>{result.issues.map(issue => <li key={issue}>{issue}</li>)}</ul></Notice>}
              {result.issues.length > 0 && <section className="clarify" aria-labelledby="clarification-title">
                <h3 id="clarification-title">A few details before we continue</h3>
                <ul>{result.questions.map(question => <li key={question}>{question}</li>)}</ul>
                {answers.length > 0 && <details><summary>Your previous answers</summary><ol>{answers.map((text, index) => <li key={index}>{text}</li>)}</ol></details>}
                {answers.length < 8 ? <form onSubmit={clarify}>
                  <label htmlFor="clarification-answer">Your answer or correction</label>
                  <textarea id="clarification-answer" value={answer} maxLength={1000} required disabled={sending} onChange={e => setAnswer(e.target.value)} />
                  <p className="field-help">Answer the questions above. We will review your full request again; nothing is saved until you confirm a complete summary.</p>
                  <button className="primary" disabled={sending || !answer.trim()}>{sending ? "Reviewing your answer…" : "Update my summary"}</button>
                </form> : <p>Please use “Correct my request” to write a complete revised request, or end here.</p>}
                <button className="text-button" disabled={sending} onClick={() => {
                  setResult(null); setMessage(""); setAnswers([]); setAnswer(""); setError(""); setEnded(true); attempt.current = null;
                }}>End request without saving</button>
              </section>}
              <div className="hero-actions">
                <button className="secondary" disabled={sending} onClick={() => { setResult(null); setAnswers([]); setAnswer(""); setError(""); document.getElementById("order-request")?.focus(); }}>Correct my request</button>
                <button className="primary" disabled={sending || !result.confirmationToken || result.issues.length > 0} onClick={confirm}>{sending ? "Saving draft…" : "Confirm and save draft"}</button>
              </div>
            </section>
          )}
        </section>
      </div>
      <section className="card orders" aria-labelledby="orders-title">
        <div className="section-heading">
          <div>
            <div className="section-number">02 / YOUR COLLECTION</div>
            <h2 id="orders-title" tabIndex={-1}>Your orders</h2>
          </div>
          <button
            className="secondary"
            disabled={loading}
            onClick={() => setReload((value) => value + 1)}
          >
            Refresh orders
          </button>
        </div>
        {trackingBusy && <Notice>Loading recorded status and explanation…</Notice>}
        {trackingError && <Notice error>{trackingError}</Notice>}
        {tracking && <section className="order-summary" aria-labelledby="tracking-title" aria-live="polite">
          <h3 id="tracking-title">Order status</h3>
          <p>Order reference: {tracking.id}</p>
          <p><strong>Recorded status: {tracking.status}</strong></p>
          <p>{tracking.explanation}</p>
          <p className="field-help">Based on saved order version {tracking.version}. Refresh to check for changes. The explanation does not change your order.</p>
          <div className="actions">
            <button className="secondary" disabled={trackingBusy} onClick={() => track(tracking.id)}>Refresh status</button>
            <button className="text-button" onClick={() => setTracking(null)}>Close status</button>
          </div>
        </section>}
        {quoteOrder && (quoteBusy || quoteError || quoteMessage || quote) && <section className="order-summary" aria-labelledby="quote-title" aria-live="polite">
          <h3 id="quote-title">Quote for order {quoteOrder.slice(0, 8)}</h3>
          {quoteBusy && <Notice>Loading your quote…</Notice>}
          {quoteError && <Notice error>{quoteError}</Notice>}
          {quoteMessage && <Notice>{quoteMessage}</Notice>}
          {quote && <>
            <p className="total-line"><strong>Total price: ${quote.total} (AUD)</strong></p>
            <p className="field-help">Quote version {quote.version}. Accepting confirms your order at this price. No payment is taken now.</p>
            <div className="actions">
              <button className="primary" disabled={quoteBusy} onClick={() => decide(true)}>Accept quote</button>
              <button className="secondary" disabled={quoteBusy} onClick={() => decide(false)}>Decline quote</button>
            </div>
          </>}
          <button className="text-button" onClick={() => { setQuoteOrder(""); setQuote(null); setQuoteMessage(""); setQuoteError(""); }}>Close quote</button>
        </section>}
        {payOrder && <section className="order-summary" aria-labelledby="pay-title" aria-live="polite">
          <h3 id="pay-title">Payment for order {payOrder.slice(0, 8)}</h3>
          {payBusy && <Notice>Contacting the payment service…</Notice>}
          {payError && <Notice error>{payError}</Notice>}
          {payMessage && <Notice>{payMessage}</Notice>}
          {payment && <>
            <p className="total-line"><strong>Amount due: ${payment.amount} (AUD)</strong></p>
            <p className="field-help">Test mode: this is a simulated payment. No card details are needed and no money moves.</p>
            <div className="actions">
              <button className="primary" disabled={payBusy} onClick={() => pay("Succeeded")}>Pay ${payment.amount} (test)</button>
              <button className="secondary" disabled={payBusy} onClick={() => pay("Failed")}>Simulate a declined payment</button>
            </div>
          </>}
          {!payBusy && !payment && payMessage.startsWith("The payment did not") && <button className="secondary" onClick={() => openPayment(payOrder)}>Try again</button>}
          <button className="text-button" onClick={() => { setPayOrder(""); setPayment(null); setPayMessage(""); setPayError(""); }}>Close payment</button>
        </section>}
        {loading ? (
          <Notice>Loading your orders…</Notice>
        ) : loadError ? (
          <Notice error>{loadError}</Notice>
        ) : orders.length === 0 ? (
          <div className="empty">
            <span aria-hidden="true">◇</span>
            <h3>No orders yet</h3>
            <p>
              Your draft orders will appear here after you confirm the summary.
            </p>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="order-list">
              <caption className="sr-only">
                Orders belonging to your signed-in account
              </caption>
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Details</th>
                  <th>Required time</th>
                  <th>Status</th>
                  <th>Next step</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id}>
                    <th scope="row" title={order.id}>{shortId(order.id)}</th>
                    <td>{order.summary}</td>
                    <td>
                      {order.requiredAt
                        ? new Intl.DateTimeFormat("en-AU", {
                            dateStyle: "medium",
                            timeStyle: "short",
                            timeZone: "Australia/Sydney",
                          }).format(new Date(order.requiredAt))
                        : "To be arranged"}
                    </td>
                    <td>
                      <span className="badge" data-status={order.status}>{order.status}</span>
                    </td>
                    <td>
                      <div className="actions">
                        {order.status === "Quoted" && <button className="primary" aria-label={`View quote for order ${order.id}`} disabled={quoteBusy} onClick={() => viewQuote(order.id)}>View quote</button>}
                        {PAYABLE.includes(order.status) && <button className="primary" aria-label={`Pay for order ${order.id}`} disabled={payBusy} onClick={() => openPayment(order.id)}>Pay</button>}
                        <button className="secondary" aria-label={`Track order ${order.id}`} disabled={trackingBusy} onClick={() => track(order.id)}>Track order</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="field-help">Times shown in Australia/Sydney.</p>
          </div>
        )}
      </section>
    </>
  );
}
