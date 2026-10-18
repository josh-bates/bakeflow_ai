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
      <section className="intro">
        <p className="eyebrow">CUSTOMER SPACE</p>
        <h1>
          A little occasion.
          <br />
          <em>Something delicious.</em>
        </h1>
        <p>
          Tell us what you have in mind. We’ll help turn the details into your
          next celebration.
        </p>
      </section>
      <div className="customer-grid">
        <section className="card" aria-labelledby="request-title">
          <div className="section-number">01 / YOUR REQUEST</div>
          <h2 id="request-title">Let’s make it yours</h2>
          <p className="muted">
            Include the product, quantity, flavours and when you need it. Add
            any customisation or allergen questions.
          </p>
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
              Submitting starts a request. It does not confirm an order or
              payment.
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
        <aside className="guidance">
          <span className="stamp" aria-hidden="true">
            ✳
          </span>
          <p className="eyebrow">THOUGHTFULLY MADE</p>
          <h2>
            Good things start
            <br />
            with the details.
          </h2>
          <ol>
            <li>
              <strong>Share your idea</strong>
              <span>Tell us about your occasion and your order.</span>
            </li>
            <li>
              <strong>Review the details</strong>
              <span>
                Any missing information needs clarification before a draft is
                saved.
              </span>
            </li>
            <li>
              <strong>Accept your quotation</strong>
              <span>
                An order is confirmed only after you accept an owner-approved
                quote.
              </span>
            </li>
          </ol>
        </aside>
      </div>
      <section className="card orders" aria-labelledby="orders-title">
        <div className="section-heading">
          <div>
            <div className="section-number">02 / KEEP IN TOUCH</div>
            <h2 id="orders-title">Your orders</h2>
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
            <h3>No saved orders yet</h3>
            <p>
              Once your request has been reviewed and saved as a draft, it will
              appear here.
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
