import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type FieldIssue,
  type Profitability,
  type ProfitLine,
  type Session,
  type TransactionInput,
} from "../api/client";
import { AiMark } from "../components/AiMark";
import { Notice } from "../components/Notice";

// UC-06 steps 4-7 (BAKE-34): profitability from the recorded ledger, and an AI
// explanation only when the owner asks. Every figure comes from the server as
// a decimal string and is shown exactly as given; the explanation never
// changes them, and if it is unavailable the figures stay on screen (5a).
//
// UC-06 steps 1-4 (BAKE-43): the owner records an income or expense through
// BAKE-24's ledger API. The server's rules decide; the page shows the fields
// to fix (2a) or that the reference is already recorded (2b), and reloads the
// figures after a save.

const money = (value: string) => {
  const negative = value.startsWith("-");
  return `${negative ? "−" : ""}$${negative ? value.slice(1) : value}`;
};
const percent = (margin: string | null) =>
  margin === null ? "—" : `${(Math.round(Number(margin) * 1000) / 10).toFixed(1)}%`;

// What the server accepts exactly: a positive amount with at most two decimals.
const AMOUNT = /^\d{1,9}(\.\d{1,2})?$/;
const FIELD_LABELS: Record<string, string> = {
  kind: "Type",
  amount: "Amount",
  source_type: "Source",
  source_reference: "Reference",
  occurred_at: "Date",
  order_id: "Order",
};
const today = () => new Intl.DateTimeFormat("en-CA").format(new Date());
const shortOrder = (id: string) => `Order ${id.slice(0, 8)}`;
type Form = Omit<TransactionInput, "occurredAt" | "orderId"> & { date: string; orderId: string };
const EMPTY: Form = { kind: "Expense", amount: "", sourceType: "Invoice", sourceReference: "", date: today(), orderId: "" };

function TransactionForm({ session, orders, onSaved }: {
  session: Session;
  orders: string[];
  onSaved: (message: string) => Promise<void>;
}) {
  const [form, setForm] = useState<Form>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const set = (change: Partial<Form>) => setForm((current) => ({ ...current, ...change }));
  const validAmount = AMOUNT.test(form.amount) && Number(form.amount) > 0;
  const ready = validAmount && form.sourceReference.trim() !== "" && form.date !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || !ready) return;
    setSaving(true);
    setError("");
    setIssues([]);
    // Two decimal places, as a string; the day starts at local midnight, so today is never "in the future".
    const [whole, cents = ""] = form.amount.split(".");
    const entry: TransactionInput = {
      kind: form.kind,
      amount: `${whole}.${cents.padEnd(2, "0")}`,
      sourceType: form.sourceType,
      sourceReference: form.sourceReference.trim(),
      occurredAt: new Date(`${form.date}T00:00:00`).toISOString(),
      orderId: form.orderId || null,
    };
    try {
      const saved = await api.recordTransaction(entry, session.csrfToken);
      setForm({ ...EMPTY, kind: form.kind, sourceType: form.sourceType, date: form.date });
      await onSaved(`${saved.kind} ${saved.sourceType} ${saved.sourceReference} of $${saved.amount} recorded.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The transaction could not be recorded. Please try again.");
      setIssues(err instanceof ApiError ? err.issues : []);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card orders" aria-labelledby="record-title">
      <h2 id="record-title">Record a transaction</h2>
      <p className="field-help">
        Enter an ingredient invoice, a receipt or a voucher. Customer payments are recorded automatically.
      </p>
      {error && (
        <Notice error>
          {error}
          {issues.length > 0 && (
            <ul>
              {issues.map((issue) => (
                <li key={`${issue.field}-${issue.message}`}>
                  {FIELD_LABELS[issue.field] ?? issue.field}: {issue.message}
                </li>
              ))}
            </ul>
          )}
        </Notice>
      )}
      <form onSubmit={submit}>
        <div className="actions">
          <label>
            Type
            <select value={form.kind} disabled={saving}
              onChange={(e) => set({ kind: e.target.value as Form["kind"] })}>
              <option value="Expense">Expense</option>
              <option value="Income">Income</option>
            </select>
          </label>
          <label>
            Amount (AUD)
            <input value={form.amount} inputMode="decimal" autoComplete="off" disabled={saving}
              aria-invalid={form.amount !== "" && !validAmount}
              onChange={(e) => set({ amount: e.target.value.trim() })} />
          </label>
          <label>
            Source
            <select value={form.sourceType} disabled={saving}
              onChange={(e) => set({ sourceType: e.target.value as Form["sourceType"] })}>
              <option value="Invoice">Invoice</option>
              <option value="Receipt">Receipt</option>
              <option value="Voucher">Voucher</option>
            </select>
          </label>
          <label>
            Reference
            <input value={form.sourceReference} maxLength={200} placeholder="INV-1042" disabled={saving}
              onChange={(e) => set({ sourceReference: e.target.value })} />
          </label>
          <label>
            Date
            <input type="date" value={form.date} max={today()} disabled={saving}
              onChange={(e) => set({ date: e.target.value })} />
          </label>
          <label>
            Order (optional)
            <select value={form.orderId} disabled={saving} onChange={(e) => set({ orderId: e.target.value })}>
              <option value="">None (general cost)</option>
              {orders.map((id) => (
                <option key={id} value={id}>{shortOrder(id)}</option>
              ))}
            </select>
          </label>
        </div>
        {form.amount !== "" && !validAmount && (
          <p className="field-help">Enter an amount above zero such as 12.50, with at most two decimals.</p>
        )}
        <button className="primary" type="submit" disabled={saving || !ready}>
          {saving ? "Recording…" : "Record transaction"}
        </button>
      </form>
    </section>
  );
}

function Row({ label, line }: { label: string; line: ProfitLine }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{money(line.income)}</td>
      <td>{money(line.expenses)}</td>
      <td>{money(line.profit)}</td>
      <td>{percent(line.margin)}</td>
    </tr>
  );
}

function Table({ caption, rows }: { caption: string; rows: { key: string; label: string; line: ProfitLine }[] }) {
  return (
    <div className="table-scroll">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{caption}</th>
            <th scope="col">Income</th>
            <th scope="col">Expenses</th>
            <th scope="col">Profit</th>
            <th scope="col">Margin</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <Row key={row.key} label={row.label} line={row.line} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FinancePage({ session }: { session: Session }) {
  const [data, setData] = useState<Profitability | null>(null);
  const [error, setError] = useState("");
  const [explanation, setExplanation] = useState<{ text: string | null; notice: string | null } | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [explainError, setExplainError] = useState("");
  const [saved, setSaved] = useState("");

  async function explain() {
    if (explaining) return;
    setExplaining(true);
    setExplainError("");
    try {
      const result = await api.explainProfitability(session.csrfToken);
      // The explanation describes exactly these figures, so show them together.
      setData(result);
      setExplanation({ text: result.explanation, notice: result.notice });
    } catch (err) {
      setExplainError(err instanceof ApiError ? err.message : "The explanation could not be requested.");
    } finally {
      setExplaining(false);
    }
  }

  const load = useCallback(
    () =>
      api.profitability().then(
        (figures) => {
          setError("");
          setData(figures);
        },
        (err: unknown) => setError(err instanceof ApiError ? err.message : "Profitability could not be loaded."),
      ),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  async function recorded(message: string) {
    setSaved(message);
    // Step 4: the figures come from the ledger again; an old explanation no longer describes them.
    setExplanation(null);
    await load();
  }

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Finance</h1>
        <p>Profit from your recorded income and expenses.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
      {saved && <Notice>{saved}</Notice>}
      <TransactionForm session={session} orders={data?.byOrder.map((o) => o.orderId) ?? []} onSaved={recorded} />
      {!data && !error && <Notice>Loading profitability…</Notice>}
      {data && (
        <section className="card orders" aria-labelledby="profit-title">
          <h2 id="profit-title">Profitability</h2>
          {data.overall.transactions === 0 ? (
            <div className="empty">
              <h3>No transactions yet</h3>
              <p>Recorded income and expenses will appear here.</p>
            </div>
          ) : (
            <>
              <Table
                caption="Overall"
                rows={[
                  { key: "overall", label: "All transactions", line: data.overall },
                  { key: "general", label: "General (no order or product)", line: data.general },
                ]}
              />
              <div className="actions">
                <button className="secondary ai" disabled={explaining} onClick={explain}>
                  <AiMark />
                  {explaining ? "Explaining…" : "Explain these figures"}
                </button>
              </div>
              {explainError && <Notice error>{explainError}</Notice>}
              {explanation?.text && (
                <section className="order-summary" aria-labelledby="explanation-title">
                  <h3 id="explanation-title">
                    <AiMark />
                    What these figures mean
                  </h3>
                  <p>{explanation.text}</p>
                  <p className="field-help">Written by the assistant from the figures above, which it cannot change.</p>
                </section>
              )}
              {explanation && !explanation.text && <Notice>{explanation.notice}</Notice>}
              <div className="notice">
                Ingredient and other general costs are usually not recorded against one order, so
                per-order and per-product margins can look higher than the bakery’s real margin. Use
                the overall profit for the business as a whole.
              </div>
              {data.byProduct.length > 0 && (
                <Table
                  caption="By product"
                  rows={data.byProduct.map((p) => ({ key: p.productId, label: p.name, line: p }))}
                />
              )}
              {data.byOrder.length > 0 && (
                <Table
                  caption="By order"
                  rows={data.byOrder.map((o) => ({
                    key: o.orderId,
                    label: shortOrder(o.orderId),
                    line: o,
                  }))}
                />
              )}
            </>
          )}
        </section>
      )}
    </>
  );
}
