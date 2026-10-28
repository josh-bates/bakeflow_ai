import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type ReceiptSource,
  type Session,
  type StockItem,
  type StockReceipt,
} from "../api/client";
import { Notice } from "../components/Notice";

// UC-03 7a (BAKE-32): the owner records delivered ingredients from an invoice
// or delivery note. Stock changes only on the server, once per receipt; every
// quantity comes from the server as a decimal string and is shown as given.

const SOURCES: Record<ReceiptSource, string> = { Invoice: "Invoice", DeliveryNote: "Delivery note" };
// What the server stores exactly: up to nine digits and three decimals.
const QUANTITY = /^\d{1,9}(\.\d{1,3})?$/;

const amount = (value: string, unit: string) =>
  `${value.startsWith("-") ? `−${value.slice(1)}` : value} ${unit}`;
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
          <section className="card receipt-form" aria-labelledby="receipt-title">
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
          <section className="card orders" aria-labelledby="stock-title">
            <h2 id="stock-title">Stock</h2>
            <p className="field-help">
              Committed stock is set aside for orders in released production plans. Available is what is left
              for new orders.
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
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id}>
                      <th scope="row">{i.ingredient}</th>
                      <td>{amount(i.onHand, i.unit)}</td>
                      <td>{amount(i.committed, i.unit)}</td>
                      <td>{amount(i.available, i.unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
