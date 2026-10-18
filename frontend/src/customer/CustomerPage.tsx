import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type Order,
  type Session,
  type Submission,
} from "../api/client";
import { Notice } from "../components/Notice";

export function CustomerPage({
  session,
  onExpired,
}: {
  session: Session;
  onExpired: () => void;
}) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Submission | null>(null);
  const pending = useRef(false);
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
      setMessage("");
      attempt.current = null;
      setReload((value) => value + 1);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else
        setError(
          `${(e as Error).message} Receipt was not confirmed. Your text has been kept; retrying the same text reuses its request reference.`,
        );
    } finally {
      pending.current = false;
      setSending(false);
    }
  }

  return (
    <>
      <section className="dessert-hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <p className="eyebrow">BAKEFLOW / CAKES & CELEBRATIONS</p>
          <h1 id="hero-title">A little indulgence.<br /><em>A special occasion.</em></h1>
          <p>Custom cakes and cupcakes, made around your occasion. Tell us what you have in mind.</p>
          <div className="hero-actions">
            <button className="primary" onClick={() => { document.getElementById('request-title')?.scrollIntoView({ block: 'start' }); document.getElementById('order-request')?.focus({ preventScroll: true }); }}>Start a custom order <span aria-hidden="true">↗</span></button>
            <button className="text-button" onClick={() => { document.getElementById('orders-title')?.scrollIntoView({ block: 'start' }); document.getElementById('orders-title')?.focus({ preventScroll: true }); }}>View my orders <span aria-hidden="true">→</span></button>
          </div>
          <p className="hero-note">Your occasion. Your details. Something thoughtfully made.</p>
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
            Include the product, quantity, flavours and when you need it. Add
            any customisation or allergen questions.
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
              onChange={(e) => {
                setMessage(e.target.value);
                setResult(null);
                setError("");
              }}
              placeholder="For example: 12 vanilla cupcakes for a birthday on 25 October at 2 pm, with blue decorations."
              aria-describedby="request-help"
            />
            <p id="request-help" className="field-help">
              We’ll review your request before confirming the details and price. No payment is taken here.
            </p>
            {error && <Notice error>{error}</Notice>}
            {result && (
              <Notice>
                <strong>Request received · {result.requestId}</strong>
                <br />
                {result.message}
              </Notice>
            )}
            <button className="primary" type="submit" disabled={sending}>
              {sending ? "Sending request…" : "Send order request"}{" "}
              <span aria-hidden="true">↗</span>
            </button>
          </form>
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
        {loading ? (
          <Notice>Loading your orders…</Notice>
        ) : loadError ? (
          <Notice error>{loadError}</Notice>
        ) : orders.length === 0 ? (
          <div className="empty">
            <span aria-hidden="true">◇</span>
            <h3>No orders yet</h3>
            <p>
              Your orders will appear here once the bakery has reviewed your request.
            </p>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <caption className="sr-only">
                Orders belonging to your signed-in account
              </caption>
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Details</th>
                  <th>Required time</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id}>
                    <th scope="row">{order.id}</th>
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
                      <span className="badge">{order.status}</span>
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
