import { useEffect, useState } from "react";
import { api, ApiError, type Profitability, type ProfitLine } from "../api/client";
import { Notice } from "../components/Notice";

// UC-06 step 4 (BAKE-34): profitability from the recorded ledger. Every figure
// comes from the server as a decimal string and is shown exactly as given.

const money = (value: string) => {
  const negative = value.startsWith("-");
  return `${negative ? "−" : ""}$${negative ? value.slice(1) : value}`;
};
const percent = (margin: string | null) =>
  margin === null ? "—" : `${(Math.round(Number(margin) * 1000) / 10).toFixed(1)}%`;

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

export function FinancePage() {
  const [data, setData] = useState<Profitability | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.profitability().then(setData, (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "Profitability could not be loaded."),
    );
  }, []);

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>Finance</h1>
        <p>Profit from your recorded income and expenses.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
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
                    label: `Order ${o.orderId.slice(0, 8)}`,
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
