import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type Order, type Session } from "../api/client";
import { changes, type ChangeKind, type ChangePreview, type ChangeRequest, type Terms } from "../api/orderChanges";
import { Notice } from "../components/Notice";
import "./orderChanges.css";

export function ProposedTerms({ terms }: { terms: Terms }) {
  return <dl className="change-terms">
    {terms.quantity != null && <><dt>Requested quantity</dt><dd>{terms.quantity}</dd></>}
    {terms.required_at && <><dt>Requested collection {terms.collection_kind ?? "time"}</dt><dd>{new Date(terms.required_at).toLocaleString()} (your local time)</dd></>}
    {terms.scope === "OwnerReview" && <><dt>Owner review</dt><dd>{terms.evidence}</dd></>}
  </dl>;
}

export function OrderChanges({ orders, session, onExpired, onChanged }: {
  orders: Order[]; session: Session; onExpired: () => void; onChanged: () => void;
}) {
  const [orderId, setOrderId] = useState("");
  const [kind, setKind] = useState<ChangeKind>("Change");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<ChangePreview | null>(null);
  const [rows, setRows] = useState<ChangeRequest[]>([]);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const pending = useRef(false);
  const reload = useCallback(() => setRevision(v => v + 1), []);
  useEffect(() => {
    let active = true;
    changes.list().then(data => { if (active) { setRows(data); setListError(""); } }).catch((e: Error) => {
      if (!active) return;
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setListError(e.message);
    });
    return () => { active = false; };
  }, [revision, onExpired]);
  async function perform(action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); }
    catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setError((e as Error).message);
      if (e instanceof ApiError && e.status === 409) { setPreview(null); reload(); }
    } finally { pending.current = false; setBusy(false); }
  }
  function edited() { setPreview(null); setNotice(""); setError(""); }
  function saved(row: ChangeRequest) {
    setNotice(row.status === "Applied" ? "Your confirmed request was saved." : row.status === "Withdrawn" ? "Request withdrawn. Your order terms are unchanged." : "Request sent for owner review. Your order terms are unchanged.");
    setPreview(null); reload(); onChanged();
  }
  return <section className="card order-changes" aria-labelledby="change-heading">
    <h2 id="change-heading">Adjust an order</h2>
    <p>Review the proposed outcome before confirming. Cancelling never issues a refund automatically.</p>
    <form onSubmit={e => { e.preventDefault(); void perform(async () => setPreview(await changes.preview(orderId, kind, message, session.csrfToken))); }}>
      <label htmlFor="change-order">Order</label>
      <select id="change-order" required value={orderId} disabled={busy} onChange={e => { setOrderId(e.target.value); edited(); }}>
        <option value="">Choose your order</option>
        {orders.map(o => <option key={o.id} value={o.id}>{o.id} — {o.summary} ({o.status})</option>)}
      </select>
      <label htmlFor="change-kind">Request type</label>
      <select id="change-kind" value={kind} disabled={busy} onChange={e => { setKind(e.target.value as ChangeKind); edited(); }}>
        <option value="Change">Change order details</option><option value="Cancel">Cancel order</option><option value="Refund">Ask the owner about a refund</option>
      </select>
      <label htmlFor="change-message">Describe your request</label>
      <textarea id="change-message" required maxLength={2000} value={message} disabled={busy} onChange={e => { setMessage(e.target.value); edited(); }} />
      <p className="field-help">For a simple change, try “change quantity to 36” or “collection at 2027-10-02T12:00:00+10:00”. Other products, recipes and allergy concerns need owner review. Make one change at a time.</p>
      <button className="primary" disabled={busy || !orderId || !message.trim()}>Preview request</button>
    </form>
    {error && <Notice error>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {preview && <section className="order-summary" aria-labelledby="change-preview-heading">
      <h3 id="change-preview-heading">Review the proposed outcome</h3>
      <p>Currently {preview.current.status}{preview.current.quantity !== null ? ` · quantity ${preview.current.quantity}` : ""} · collection {preview.current.collectionKind} {new Date(preview.current.requiredAt).toLocaleString()} (your local time)</p>
      <ProposedTerms terms={preview.proposed} /><p>{preview.outcome}</p>
      <p>Nothing has been saved yet.</p>
      <div className="change-actions">
        <button className="primary" disabled={busy} onClick={() => { void perform(async () => saved(await changes.confirm(preview.confirmationToken, session.csrfToken))); }}>Confirm this request</button>
        <button className="secondary" disabled={busy} onClick={() => { setPreview(null); setNotice("Preview withdrawn. Your order is unchanged."); }}>Withdraw without saving</button>
      </div>
    </section>}
    <div className="section-heading"><h3>Your change requests</h3><button className="secondary" disabled={busy} onClick={reload}>Refresh requests</button></div>
    {listError && <Notice error>{listError}</Notice>}
    {!listError && rows.length === 0 && <p>No confirmed change requests yet.</p>}
    {rows.map(row => <article className="change-request" key={row.id}>
      <h4>{row.kind} · {row.status}</h4><p>Order {row.orderId}</p><p>{row.message}</p>
      <ProposedTerms terms={row.proposed} />
      {row.ownerNote && <p>Owner: {row.ownerNote}</p>}
      {row.quoteTotal !== null && <p>Revised quotation: <strong>AUD {row.quoteTotal}</strong></p>}
      <div className="change-actions">
        {row.status === "Quoted" && <button className="primary" disabled={busy} onClick={() => { void perform(async () => saved(await changes.action(row.id, row.version, "accept", session.csrfToken))); }}>Accept revised quotation and apply change</button>}
        {["Pending", "AwaitingApproval", "Quoted"].includes(row.status) && <button className="secondary" disabled={busy} onClick={() => { void perform(async () => saved(await changes.action(row.id, row.version, "withdraw", session.csrfToken))); }}>Withdraw request</button>}
      </div>
    </article>)}
  </section>;
}
