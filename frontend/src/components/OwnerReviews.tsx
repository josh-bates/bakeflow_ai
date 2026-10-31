import { useEffect, useState } from "react";
import { ApiError, type Session } from "../api/client";
import { referrals, type Referral, type CatalogueItem, type Resolution, type ReviewedSummary } from "../api/referrals";
import { Notice } from "./Notice";

const empty: ReviewedSummary = { items: [], required_at: null, collection_kind: "at", allergen_concerns: null, allergen_statement: null, budget: null };
const labels = { Pending: "Waiting for the owner", NeedsClarification: "More information needed", AwaitingCustomer: "Ready for your review", Completed: "Review completed", Closed: "Closed" };

function Summary({ data }: { data: ReviewedSummary }) {
  return <dl>
    <dt>Items</dt><dd>{data.items.map((i, n) => <div key={n}>{i.quantity ?? "Quantity needed"} × {i.product}{i.flavour && ` · ${i.flavour}`}{i.theme && ` · ${i.theme}`}{i.customisation && ` · ${i.customisation}`}</div>)}</dd>
    <dt>Collection</dt><dd>{data.collection_kind} {data.required_at || "Not supplied"}</dd>
    <dt>Allergen concerns</dt><dd>{data.allergen_concerns === null ? "Not yet resolved" : data.allergen_concerns.join(", ") || "No concerns stated"}</dd>
    <dt>Budget</dt><dd>{data.budget ?? "Not supplied"}</dd>
  </dl>;
}

function ResolutionForm({ row, catalogue, disabled, save }: { row: Referral; catalogue: CatalogueItem[]; disabled: boolean; save: (resolution: Resolution) => void }) {
  const [data, setData] = useState<ReviewedSummary>(row.summary ?? empty);
  const [selected, setSelected] = useState<string[]>((row.summary?.items ?? []).map(() => ""));
  const [note, setNote] = useState("");
  const [disclosure, setDisclosure] = useState(row.summary?.allergen_concerns?.join(", ") ?? "");
  const [noConcerns, setNoConcerns] = useState(false);
  function changeItem(index: number, field: string, value: unknown) {
    setData(d => ({ ...d, items: d.items.map((item, n) => n === index ? { ...item, [field]: value } : item) }));
  }
  return <form onSubmit={e => {
    e.preventDefault();
    if (row.kind !== "Intake") return save({ summary: null, recipes: [], note });
    const choices = selected.map(id => catalogue.find(p => p.productId === id));
    if (choices.some(c => !c) || choices.length !== data.items.length) return;
    save({ note, summary: { ...data, allergen_concerns: noConcerns ? [] : disclosure.split(",").map(s => s.trim()).filter(Boolean),
      allergen_statement: noConcerns ? "Owner reviewed the disclosure; customer must confirm no concerns." : disclosure },
      recipes: choices.map(c => ({ productId: c!.productId, recipeId: c!.recipeId, recipeVersion: c!.recipeVersion })) });
  }}>
    {row.kind === "Intake" && <>
      <p>Select an existing product and recipe for every item. Review the original disclosure and ingredients; a catalogue selection is not an automatic allergen-safety guarantee.</p>
      {data.items.map((item, i) => <fieldset key={i} disabled={disabled}>
        <legend>Item {i + 1}: {item.product}</legend>
        <label>Product and recipe<select required value={selected[i] || ""} onChange={e => {
          const product = catalogue.find(p => p.productId === e.target.value)!;
          setSelected(s => s.map((v, n) => n === i ? product.productId : v));
          changeItem(i, "product", product.name);
        }}><option value="">Choose after reviewing ingredients</option>{catalogue.map(p => <option key={p.productId} value={p.productId}>{p.name} · recipe v{p.recipeVersion}</option>)}</select></label>
        {selected[i] && <p>Ingredients: {catalogue.find(p => p.productId === selected[i])?.ingredients.join(", ")}</p>}
        <label>Quantity<input type="number" min="1" max="10000" step="1" required value={item.quantity ?? ""} onChange={e => changeItem(i, "quantity", e.target.value ? Number(e.target.value) : null)} /></label>
        <label>Flavour<input maxLength={200} value={item.flavour ?? ""} onChange={e => changeItem(i, "flavour", e.target.value || null)} /></label>
        <label>Theme<input maxLength={500} value={item.theme ?? ""} onChange={e => changeItem(i, "theme", e.target.value || null)} /></label>
        <label>Agreed customisation<input maxLength={1000} value={item.customisation ?? ""} onChange={e => changeItem(i, "customisation", e.target.value || null)} /></label>
        <p>Free text does not change the selected recipe.</p>
      </fieldset>)}
      {!data.items.length && <button type="button" disabled={disabled} onClick={() => { setData(d => ({ ...d, items: [{ product: "", quantity: null, flavour: null, theme: null, customisation: null }] })); setSelected([""]); }}>Add an item</button>}
      <label>Collection date and time (include timezone)<input required disabled={disabled} placeholder="2026-11-05T12:00:00+11:00" value={data.required_at ?? ""} onChange={e => setData(d => ({ ...d, required_at: e.target.value }))} /></label>
      <label>Collection timing<select disabled={disabled} value={data.collection_kind} onChange={e => setData(d => ({ ...d, collection_kind: e.target.value as "at" | "before" }))}><option value="at">At</option><option value="before">Before</option></select></label>
      <label>Reviewed allergen concerns<input disabled={disabled || noConcerns} required={!noConcerns} maxLength={1000} value={disclosure} onChange={e => setDisclosure(e.target.value)} /></label>
      <label><input type="checkbox" disabled={disabled} checked={noConcerns} onChange={e => setNoConcerns(e.target.checked)} /> I have clarified that the customer has no allergen concerns</label>
      <label>Optional budget<input disabled={disabled} type="number" min="0" step="0.01" value={data.budget ?? ""} onChange={e => setData(d => ({ ...d, budget: e.target.value || null }))} /></label>
    </>}
    <label>{row.kind === "Intake" ? "Explain the selected recipe and agreed handling" : "Proposed handling for the customer"}<textarea required disabled={disabled} maxLength={2000} value={note} onChange={e => setNote(e.target.value)} /></label>
    {row.kind !== "Intake" && <p>This records a reviewed proposal only. It does not issue a refund, change the order or alter production.</p>}
    <button className="primary" disabled={disabled || (row.kind === "Intake" && !data.items.length)}>Send resolution to customer</button>
  </form>;
}

function ReviewCard({ row, owner, catalogue, session, changed, expired }: {
  row: Referral; owner: boolean; catalogue: CatalogueItem[]; session: Session; changed: () => void; expired?: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function act(action: "question" | "reply" | "resolve" | "confirm", fields = {}) {
    if (busy) return;
    setBusy(true); setError("");
    try { await referrals.action(row, action, session.csrfToken, fields); setText(""); changed(); }
    catch (e) { if (e instanceof ApiError && e.status === 401) expired?.(); setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <article className="card" aria-label={`Review ${row.id}`}>
    <span className="badge">{labels[row.status]}</span><h3>{row.kind === "Intake" ? "Custom order review" : row.kind === "Refund" ? "Refund review" : "Production change review"}</h3>
    <p className="muted">Reference {row.id} · version {row.version}</p>
    <h4>Original request</h4><p style={{ whiteSpace: "pre-wrap" }}>{row.message}</p>
    {row.orderId && <p>Order: {row.orderId}</p>}
    {row.conversation.map((m, i) => <p key={i}><strong>{m.role}:</strong> {m.text}</p>)}
    {row.resolution && <section aria-label="Owner resolution"><h4>Owner resolution</h4><p>{row.resolution.note}</p>{row.resolution.summary && <Summary data={row.resolution.summary} />}</section>}
    {error && <Notice error>{error}</Notice>}
    {owner && row.status === "Pending" && <ResolutionForm row={row} catalogue={catalogue} disabled={busy} save={resolution => void act("resolve", { resolution })} />}
    {((owner && ["Pending", "AwaitingCustomer"].includes(row.status)) || (!owner && ["NeedsClarification", "AwaitingCustomer"].includes(row.status))) &&
      <form onSubmit={e => { e.preventDefault(); void act(owner ? "question" : "reply", { text }); }}>
        <label>{owner ? "Question for the customer" : "Answer or correction"}<textarea required maxLength={2000} disabled={busy} value={text} onChange={e => setText(e.target.value)} /></label>
        <button className="secondary" disabled={busy}>{owner ? "Ask for clarification" : "Send to owner for review"}</button>
      </form>}
    {!owner && row.status === "AwaitingCustomer" && <button className="primary" disabled={busy} onClick={() => void act("confirm")}>{row.kind === "Intake" ? "Confirm reviewed details and save draft" : "Acknowledge reviewed proposal"}</button>}
    {row.status === "Completed" && <Notice>{row.kind === "Intake" ? "Draft saved after your confirmation. It is not a confirmed sale." : "Proposal acknowledged. No refund, order change or production change has been applied by this review."}</Notice>}
  </article>;
}

export function OwnerReviews({ session, refreshKey, onExpired }: { session: Session; refreshKey?: unknown; onExpired?: () => void }) {
  const owner = session.user.role === "Owner";
  const [rows, setRows] = useState<Referral[]>([]);
  const [catalogue, setCatalogue] = useState<CatalogueItem[]>([]);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [orderId, setOrderId] = useState("");
  const [kind, setKind] = useState<"Refund" | "InProductionChange">("Refund");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    Promise.all([referrals.list(), owner ? referrals.catalogue() : Promise.resolve([])]).then(([r, c]) => {
      if (active) { setRows(r); setCatalogue(c); }
    }).catch(e => { if (active) { setError(e.message); if (e instanceof ApiError && e.status === 401) onExpired?.(); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [owner, reload, refreshKey, onExpired]);
  return <section aria-labelledby="owner-reviews-title">
    <h2 id="owner-reviews-title">{owner ? "Order exceptions awaiting review" : "Owner reviews"}</h2>
    <p>Custom recipes and uncertain allergen details wait for the owner before an order draft is saved.</p>
    <button className="secondary" disabled={loading} onClick={() => setReload(n => n + 1)}>Refresh reviews</button>
    {loading ? <p>Loading reviews…</p> : error ? <Notice error>{error}</Notice> : <>
      {!rows.length && <p>No owner reviews yet.</p>}
      {rows.map(row => <ReviewCard key={`${row.id}:${row.version}:${row.status}`} row={row} session={session} owner={owner} catalogue={catalogue} changed={() => setReload(n => n + 1)} expired={onExpired} />)}
    </>}
    {!owner && <details><summary>Request a refund or a change to work in production</summary><form onSubmit={async e => {
      e.preventDefault(); if (busy) return; setBusy(true); setError("");
      try { await referrals.create(kind, orderId.trim(), message, session.csrfToken); setMessage(""); setReload(n => n + 1); }
      catch (e) { setError((e as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>The owner will review your request. Sending it does not change your order or issue a refund.</p>
      <label>Order reference<input required disabled={busy} value={orderId} onChange={e => setOrderId(e.target.value)} /></label>
      <label>Request type<select disabled={busy} value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="Refund">Refund review</option><option value="InProductionChange">Change to work in production</option></select></label>
      <label>Describe your request<textarea required disabled={busy} maxLength={4000} value={message} onChange={e => setMessage(e.target.value)} /></label>
      <button className="primary" disabled={busy}>Request owner review</button>
    </form></details>}
  </section>;
}
