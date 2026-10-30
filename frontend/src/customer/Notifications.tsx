import { useEffect, useState } from "react";
import { api, ApiError, type OrderNotification } from "../api/client";
import { Notice } from "../components/Notice";

export function Notifications({ refresh, onExpired }: { refresh: number; onExpired: () => void }) {
  const [items, setItems] = useState<OrderNotification[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true;
      try {
        const next = await api.notifications();
        if (active) { setItems(next); setError(""); }
      } catch (e) {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401) onExpired();
        else setError((e as Error).message);
      } finally {
        pending = false;
        if (active) setLoading(false);
      }
    }
    void load();
    const timer = window.setInterval(() => { void load(); }, 15000);
    const focus = () => { void load(); };
    window.addEventListener("focus", focus);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [refresh, retry, onExpired]);

  return <section className="card order-notifications" aria-labelledby="notifications-heading">
    <h2 id="notifications-heading">Order updates</h2>
    <p>Updates appear here automatically when your order moves to a new stage.</p>
    {loading && <p role="status">Loading updates…</p>}
    {error && <><Notice error>{error}</Notice><button type="button" className="button secondary" onClick={() => setRetry(n => n + 1)}>Retry updates</button></>}
    <p role="status" aria-live="polite">{!loading && !error && (items.length ? `${items.length} order update${items.length === 1 ? "" : "s"}` : "No updates yet. Your first update will appear after you save a draft.")}</p>
    {items.length > 0 && <details open>
      <summary>Notification history</summary>
      <ul className="notification-list">
        {items.map(item => <li key={item.id}>
          <div><strong>{item.status === "InProduction" ? "In production" : item.status}</strong> <time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time></div>
          <p>{item.message}</p>
        </li>)}
      </ul>
    </details>}
  </section>;
}
