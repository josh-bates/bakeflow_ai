import { useEffect, useState } from "react";
import { api, ApiError, DATA_CHANGED, type OwnerOrder } from "../api/client";
import { Notice } from "../components/Notice";

// Every order and where it stands. An accepted quote takes an order out of
// Pending approvals, so this is where the owner sees it accepted and paid.

const short = (id: string) => id.slice(0, 8);
const when = (iso: string) =>
  new Intl.DateTimeFormat("en-AU", { dateStyle: "medium", timeStyle: "short", timeZone: "Australia/Sydney" }).format(new Date(iso));
const STATUS: Record<string, string> = { AwaitingApproval: "Awaiting approval", InProduction: "In production" };

function Quote({ order }: { order: OwnerOrder }) {
  if (!order.quote) return <span className="field-help">Not priced yet</span>;
  const decision = order.accepted ? "accepted" : order.quote.customerDecision === "Rejected" ? "rejected" : "with the customer";
  return <>${order.quote.total} <span className="field-help">· {decision}</span></>;
}

const PAYMENT: Record<string, string> = { Succeeded: "Paid", Created: "Payment started", Failed: "Payment failed" };

export function AllOrders({ onExpired }: { onExpired?: () => void }) {
  const [orders, setOrders] = useState<OwnerOrder[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      api.ownerOrders().then(
        (rows) => { if (active) { setOrders(rows); setError(""); } },
        (e: unknown) => {
          if (!active) return;
          if (e instanceof ApiError && e.status === 401) onExpired?.();
          else setError(e instanceof ApiError ? e.message : "Orders could not be loaded.");
        },
      );
    load();
    window.addEventListener(DATA_CHANGED, load);
    return () => { active = false; window.removeEventListener(DATA_CHANGED, load); };
  }, [onExpired]);

  return (
    <section className="card orders" aria-labelledby="all-orders-title">
      <h2 id="all-orders-title">All orders</h2>
      <p className="field-help">Soonest collection first. Times shown in Australia/Sydney.</p>
      {error && <Notice error>{error}</Notice>}
      {!orders && !error && <Notice>Loading orders…</Notice>}
      {orders?.length === 0 && <div className="empty"><h3>No orders yet</h3></div>}
      {orders && orders.length > 0 && (
        <div className="table-scroll">
          <table className="order-list">
            <thead>
              <tr>
                <th scope="col">Order</th>
                <th scope="col">Customer</th>
                <th scope="col">Details</th>
                <th scope="col">Collection</th>
                <th scope="col">Status</th>
                <th scope="col">Quote</th>
                <th scope="col">Payment</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <th scope="row" title={order.id}>{short(order.id)}</th>
                  <td>{order.customer}</td>
                  <td>{order.summary}</td>
                  <td>{when(order.requiredAt)}</td>
                  <td><span className="badge" data-status={order.status}>{STATUS[order.status] ?? order.status}</span></td>
                  <td><Quote order={order} /></td>
                  <td>{order.payment ? PAYMENT[order.payment] ?? order.payment : <span className="field-help">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
