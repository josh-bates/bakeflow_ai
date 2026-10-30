import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type LowStockExplanation,
  type ReceiptSource,
  type Session,
  type StockItem,
  type StockReceipt,
} from "../api/client";
import { AiMark } from "../components/AiMark";
import { Notice } from "../components/Notice";

// UC-03 7a (BAKE-32): the owner records delivered ingredients from an invoice
// or delivery note. Stock changes only on the server, once per receipt; every
// quantity comes from the server as a decimal string and stays one: messages show
// it as given, labels and the stock table only tidy how it reads.
// BAKE-37 [O1]: ingredients below their reorder level are listed with the
// server's suggested reorder; the owner sets reorder levels and may ask the
// assistant to explain the alerts. Nothing is ordered for the owner.

const SOURCES: Record<ReceiptSource, string> = { Invoice: "Invoice", DeliveryNote: "Delivery note" };
// What the server stores exactly: up to nine digits and three decimals.
const QUANTITY = /^\d{1,9}(\.\d{1,3})?$/;

const amount = (value: string, unit: string) =>
  `${value.startsWith("-") ? `−${value.slice(1)}` : value} ${unit}`;
// 3500.000 reads as 3,500 and 12.500 as 12.5. Works on the text, so no digit is ever rounded.
export function readableQuantity(value: string): string {
  const unsigned = value.replace(/^-/, "");
  const [whole, decimals = ""] = unsigned.split(".");
  const fraction = decimals.replace(/0+$/, "");
  const sign = value.startsWith("-") && /[1-9]/.test(unsigned) ? "−" : "";
  return `${sign}${whole.replace(/\B(?=(\d{3})+$)/g, ",")}${fraction && `.${fraction}`}`;
}
const readableAmount = (value: string, unit: string) => `${readableQuantity(value)} ${unit}`;
const failure = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function receiptMessage(r: StockReceipt): string {
  const source = `${SOURCES[r.sourceType]} ${r.sourceReference}`;
  if (r.replayed)
    return `${source} was already recorded for ${r.ingredient} (${amount(r.quantity, r.unit)}). Stock was not raised again.`;
  return `Recorded ${amount(r.quantity, r.unit)} of ${r.ingredient} from ${source}. ${r.ingredient} on hand is now ${amount(r.onHand, r.unit)}.`;
}

export function InventoryPage({ session }: { session: Session }) {
  const [items, setItems] = useState<StockItem[] | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [itemId, setItemId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [sourceType, setSourceType] = useState<ReceiptSource>("Invoice");
  const [reference, setReference] = useState("");
  const [levels, setLevels] = useState<Record<string, string>>({});
  const [explanation, setExplanation] = useState<LowStockExplanation | null>(null);

  const load = useCallback(
    () =>
      api.stock().then(
        (value) => {
          setItems(value);
          setItemId((current) => current || value[0]?.id || "");
        },
        (err: unknown) => setError(failure(err, "Stock levels could not be loaded.")),
      ),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  const item = items?.find((i) => i.id === itemId);
  const quantityValid = QUANTITY.test(quantity) && /[1-9]/.test(quantity);
  const valid = Boolean(item && quantityValid && reference.trim());

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await action();
      await load();
    } catch (err) {
      setError(failure(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  const saveLevel = (i: StockItem, level: string) =>
    run(async () => {
      const saved = await api.setReorderLevel(i.id, level, i.version, session.csrfToken);
      setLevels(({ [i.id]: _saved, ...rest }) => rest);
      setExplanation(null);
      setMessage(`${saved.ingredient} reorder level is now ${amount(saved.reorderLevel, saved.unit)}.`);
    }, "The reorder level could not be saved. Nothing was changed.");

  const explain = () =>
    run(async () => {
      setExplanation(null);
      setExplanation(await api.explainLowStock(session.csrfToken));
    }, "The explanation could not be loaded. The low-stock list is unchanged.");

  async function record(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const saved = await api.receiveStock(
        { itemId, quantity, sourceType, sourceReference: reference.trim() },
        session.csrfToken,
      );
      setMessage(receiptMessage(saved));
      // Stock may have changed, so an earlier explanation could describe old figures.
      setExplanation(null);
      setQuantity("");
      setReference("");
      await load();
    } catch (err) {
      setError(failure(err, "The receipt could not be recorded. Stock was not changed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Inventory</h1>
        <p>Know what’s on hand before you start.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
      {message && <Notice>{message}</Notice>}
      {!items && !error && <Notice>Loading stock levels…</Notice>}
      {items && (
        <>
          <div className="plan-grid stock-grid">
            <LowStock
              items={items.filter((i) => i.low)}
              busy={busy}
              explanation={explanation}
              onExplain={explain}
            />
            <section className="card plan-form receipt-form" aria-labelledby="receipt-title">
              <h2 id="receipt-title">Record a receipt</h2>
              {items.length === 0 ? (
                <p className="field-help">No ingredients are set up yet.</p>
              ) : (
                <form onSubmit={record}>
                  <div className="actions">
                    <label>
                      Ingredient
                      <select value={itemId} disabled={busy} onChange={(e) => setItemId(e.target.value)}>
                        {items.map((i) => (
                          <option key={i.id} value={i.id}>
                            {i.ingredient} ({i.unit})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Quantity received{item ? ` (${item.unit})` : ""}
                      <input
                        inputMode="decimal"
                        autoComplete="off"
                        value={quantity}
                        required
                        disabled={busy}
                        aria-invalid={quantity !== "" && !quantityValid}
                        aria-describedby="receipt-help"
                        onChange={(e) => setQuantity(e.target.value.trim())}
                      />
                    </label>
                    <label>
                      Source
                      <select
                        value={sourceType}
                        disabled={busy}
                        onChange={(e) => setSourceType(e.target.value as ReceiptSource)}
                      >
                        {Object.entries(SOURCES).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Reference number
                      <input
                        autoComplete="off"
                        maxLength={200}
                        value={reference}
                        required
                        disabled={busy}
                        onChange={(e) => setReference(e.target.value)}
                      />
                    </label>
                  </div>
                  <p id="receipt-help" className="field-help">
                    {quantity !== "" && !quantityValid
                      ? "Enter a quantity above zero with at most three decimal places, in the ingredient’s stock unit."
                      : "Use the number printed on the invoice or delivery note. Recording the same receipt again does not add the stock twice; a delivery with several ingredients needs one receipt per ingredient."}
                  </p>
                  <button className="primary" type="submit" disabled={busy || !valid}>
                    {busy ? "Recording…" : "Record receipt"}
                  </button>
                </form>
              )}
            </section>
          </div>
          <section className="card orders stock-levels" aria-labelledby="stock-title">
            <h2 id="stock-title">Stock</h2>
            <p className="field-help">
              Committed stock is set aside for orders in released production plans. Available is what is left
              for new orders. An ingredient is low when available stock falls below its reorder level, in its
              stock unit; a level of 0 alerts only when plans need more than is on hand.
            </p>
            <div className="table-scroll">
              <table>
                <caption className="sr-only">Stock levels</caption>
                <thead>
                  <tr>
                    <th scope="col">Ingredient</th>
                    <th scope="col">On hand</th>
                    <th scope="col">Committed</th>
                    <th scope="col">Available</th>
                    <th scope="col">Reorder level</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => {
                    const draft = levels[i.id] ?? i.reorderLevel;
                    const draftValid = QUANTITY.test(draft);
                    return (
                      <tr key={i.id} className={i.low ? "low" : undefined}>
                        <th scope="row">
                          {i.ingredient}
                          {i.low && (
                            <>
                              {" "}
                              <span className="badge" data-status="Low">Low</span>
                            </>
                          )}
                        </th>
                        <td>{readableAmount(i.onHand, i.unit)}</td>
                        <td>{readableAmount(i.committed, i.unit)}</td>
                        <td>{readableAmount(i.available, i.unit)}</td>
                        <td>
                          <form
                            className="inline-form"
                            data-edited={draft !== i.reorderLevel}
                            onSubmit={(e) => {
                              e.preventDefault();
                              if (draftValid && draft !== i.reorderLevel && !busy) saveLevel(i, draft);
                            }}
                          >
                            <input
                              inputMode="decimal"
                              autoComplete="off"
                              size={10}
                              aria-label={`Reorder level for ${i.ingredient} (${i.unit})`}
                              aria-invalid={!draftValid}
                              value={draft}
                              disabled={busy}
                              onChange={(e) => setLevels((d) => ({ ...d, [i.id]: e.target.value.trim() }))}
                            />{" "}
                            <button
                              className="secondary"
                              type="submit"
                              aria-label={`Save the reorder level for ${i.ingredient}`}
                              disabled={busy || !draftValid || draft === i.reorderLevel}
                            >
                              Save
                            </button>
                          </form>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}

const NO_ALERTS = "Nothing is below its reorder level now.";

// Bar length only, never shown as a figure: the middle mark is the reorder level
// and the full bar is twice the level, where a suggested reorder would bring stock.
const gaugeFill = (i: StockItem) => {
  const full = 2 * Number(i.reorderLevel);
  return full > 0 ? Math.min(100, Math.max(0, (Number(i.available) / full) * 100)) : 0;
};

function LowStock({
  items,
  busy,
  explanation,
  onExplain,
}: {
  items: StockItem[];
  busy: boolean;
  explanation: LowStockExplanation | null;
  onExplain: () => void;
}) {
  return (
    <section className="card schedule low-stock" aria-labelledby="low-stock-title">
      <div className={`lid ${items.length ? "apricot" : "mint"}`}>
        <span className="caps">{items.length ? "Needs attention" : "All clear"}</span>
        {items.length > 0 && (
          <span className="badge" data-status="Low">
            {items.length} below reorder level
          </span>
        )}
      </div>
      <div className="plan-body">
        {items.length === 0 ? (
          <>
            <h2 id="low-stock-title">Low stock</h2>
            <p className="field-help">Nothing is below its reorder level.</p>
          </>
        ) : (
          <>
            <div className="section-heading">
              <h2 id="low-stock-title">
                {items.length} {items.length === 1 ? "ingredient is" : "ingredients are"} running <em>low</em>
              </h2>
              <button className="secondary ai" type="button" disabled={busy} onClick={onExplain}>
                <AiMark />
                {busy ? "Working…" : "Explain these alerts"}
              </button>
            </div>
            <p className="field-help">
              These are below their reorder level once released plans take what they need. The suggested reorder
              brings available stock back to twice the level. Nothing is ordered for you.
            </p>
            {/* The labels are for the eye; screen readers get the same figures from the table below. */}
            <div className={items.length > 4 ? "labels compact" : "labels"} aria-hidden="true">
              {items.map((i) => (
                <div className="etq" key={i.id}>
                  <div className="in">
                    <span className="name">{i.ingredient}</span>
                    <span className="caps">Suggested reorder</span>
                    <span className="num">
                      {i.suggestedReorder && readableQuantity(i.suggestedReorder)}
                      <small>{i.unit}</small>
                    </span>
                    <div className="gauge">
                      <i style={{ width: `${gaugeFill(i)}%` }} />
                      <b />
                    </div>
                    <div className="facts">
                      <span>
                        <b>{readableAmount(i.available, i.unit)}</b> available
                      </span>
                      <span>
                        level <b>{readableAmount(i.reorderLevel, i.unit)}</b>
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <div className="sr-only">
              <table>
                <caption>Low-stock alerts</caption>
                <thead>
                  <tr>
                    <th scope="col">Ingredient</th>
                    <th scope="col">Available</th>
                    <th scope="col">Reorder level</th>
                    <th scope="col">Suggested reorder</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id}>
                      <th scope="row">{i.ingredient}</th>
                      <td>{amount(i.available, i.unit)}</td>
                      <td>{amount(i.reorderLevel, i.unit)}</td>
                      <td className="reorder">{i.suggestedReorder && amount(i.suggestedReorder, i.unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {explanation &&
          (explanation.explanation ? (
            <div className="note-ai" role="status">
              <AiMark />
              <p>
                <strong>Assistant’s explanation:</strong> {explanation.explanation}
              </p>
            </div>
          ) : (
            <div className="notice" role="status">
              {explanation.notice ?? NO_ALERTS}
            </div>
          ))}
      </div>
    </section>
  );
}
