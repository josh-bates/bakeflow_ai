import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { Notice } from "./Notice";

export function SignIn({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await api.login(email, password); setPassword(""); onSuccess(); }
    catch (e) { setError((e as Error).message); setPassword(""); }
    finally { setBusy(false); }
  }
  return <section className="card access sign-in">
    <p className="eyebrow">WELCOME TO BAKEFLOW</p>
    <h1>A little <em>more baking.</em></h1>
    <p>Sign in to manage your orders or your bakery.</p>
    <form onSubmit={submit}>
      <label htmlFor="email">Email</label>
      <input id="email" type="email" autoComplete="username" maxLength={320} required value={email} onChange={e => setEmail(e.target.value)} disabled={busy} />
      <label htmlFor="password">Password</label>
      <input id="password" type="password" autoComplete="current-password" maxLength={1024} required value={password} onChange={e => setPassword(e.target.value)} disabled={busy} />
      {error && <Notice error>{error}</Notice>}
      <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  </section>;
}
