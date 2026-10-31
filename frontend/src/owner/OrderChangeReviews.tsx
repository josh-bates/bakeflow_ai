import { useEffect, useState } from "react";
import { ApiError, type Session } from "../api/client";
import { changes, type ChangeRequest } from "../api/orderChanges";
import { ProposedTerms } from "../customer/OrderChanges";
import { Notice } from "../components/Notice";

function Review({ row, session, updated, onExpired }: { row: ChangeRequest; session: Session; updated: () => void; onExpired?: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function decide(decision: string) {
    if (busy || !note.trim()) return;
    setBusy(true); setError("");
    try { await changes.decide(row.id, row.version, decision, note, session.csrfToken); updated(); }
    catch (e) { if (e instanceof ApiError && e.status === 401) { onExpired?.(); return; } setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <article className="change-request">
    <h3>{row.kind} · {row.status}</h3><p>Order #{row.orderId.slice(0, 8)}</p><p>{row.message}</p>
    <ProposedTerms terms={row.proposed} />
    {row.quoteTotal !== null && <p>Calculated revised quotation: <strong>AUD {row.quoteTotal}</strong></p>}
    {row.ownerNote && <p>Last review note: {row.ownerNote}</p>}
    {row.kind === "Refund" && <p className="field-help refund-path">BakeFlow doesn't move money. Refund the customer yourself, then 
        <a href="#/owner/finance">record the refund in Finance</a> as an expense, for example Voucher REFUND-{row.orderId.slice(0, 8)}, so the profit figures stay right.</p>}
    {["Pending", "AwaitingApproval", "Quoted"].includes(row.status) && <>
      <label htmlFor={`review-${row.id}`}>Reply to customer</label>
      <textarea id={`review-${row.id}`} value={note} maxLength={2000} disabled={busy} onChange={e => setNote(e.target.value)} />
      <div className="change-actions">
        {row.status === "Pending" && row.proposed.route === "Requote" && <button className="primary" disabled={busy || !note.trim()} onClick={() => { void decide("PrepareQuote"); }}>Prepare revised quotation</button>}
        {row.status === "AwaitingApproval" && <button className="primary" disabled={busy || !note.trim()} onClick={() => { void decide("ApproveQuote"); }}>Approve this revised quotation</button>}
        <button className="secondary" disabled={busy || !note.trim()} onClick={() => { void decide("Clarify"); }}>Send review note</button>
        <button className="secondary" disabled={busy || !note.trim()} onClick={() => { void decide("Reject"); }}>Decline request</button>
      </div>
      <p className="field-help">Refunds, recipe exceptions and started work require a separate owner resolution. These controls do not refund money or change started work.</p>
    </>}
    {error && <Notice error>{error}</Notice>}
  </article>;
}
export function OrderChangeReviews({ session, onExpired }: { session: Session; onExpired?: () => void }) {
  const [rows, setRows] = useState<ChangeRequest[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    changes.list(true).then(data => { if (active) { setRows(data); setError(""); } }).catch((e: Error) => { if (active) { if (e instanceof ApiError && e.status === 401) onExpired?.(); else setError(e.message); } });
    return () => { active = false; };
  }, [reload, onExpired]);
  return <section className="card order-changes">
    <div className="section-heading"><h2>Order change reviews</h2><button className="secondary" onClick={() => setReload(n => n + 1)}>Refresh reviews</button></div>
    <p>Review customer-confirmed amendments and cancellations. Existing commercial terms remain in force until an approved revised quotation is accepted.</p>
    {error && <Notice error>{error}</Notice>}
    {!error && rows.length === 0 && <p>No order change requests.</p>}
    {rows.map(row => <Review key={`${row.id}-${row.version}`} row={row} session={session} onExpired={onExpired} updated={() => setReload(n => n + 1)} />)}
  </section>;
}
