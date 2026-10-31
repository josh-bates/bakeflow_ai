import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Session } from "./api/client";
import { CustomerPage } from "./customer/CustomerPage";
import { OwnerPage, ownerPages, type OwnerPageName } from "./owner/OwnerPage";
import { SignIn } from "./components/SignIn";
import { Notice } from "./components/Notice";

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [signedOut, setSignedOut] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [path, setPath] = useState(location.hash.slice(1) || "/");
  useEffect(() => {
    const change = () => setPath(location.hash.slice(1) || "/");
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setSignedOut(false);
    setSession(null);
    api
      .session()
      .then((value) => {
        if (active) setSession(value);
      })
      .catch((e: Error) => {
        if (!active) return;
        setSignedOut(e instanceof ApiError && e.status === 401);
        setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  const expire = useCallback(() => {
    setSession(null);
    setSignedOut(true);
    setError("Your session has ended. Please sign in again.");
  }, []);
  const route =
    (path === "/" || path === "/login")
      ? session?.user.role === "Owner"
        ? "/owner/approvals"
        : "/customer"
      : path;
  const ownerPage = route.split("/")[2] as OwnerPageName;
  const permitted =
    session?.user.role === "Customer"
      ? route === "/customer"
      : route.startsWith("/owner/") && Object.hasOwn(ownerPages, ownerPage);
  const home =
    session?.user.role === "Owner" ? "/owner/approvals" : "/customer";

  return (
    <>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to content
      </a>
      <header className="topbar">
        <a className="brand" href={`#${home}`}>
          <span className="brand-mark" aria-hidden="true">
            B
          </span>
          BakeFlow<span className="brand-small">BAKERY & MORE</span>
        </a>
        {session && (
          <div className="identity">
            <span className="avatar" aria-hidden="true">
              {session.user.displayName.charAt(0)}
            </span>
            <span>
              {session.user.displayName}
              <small>{session.user.role}</small>
            </span>
            <button className="secondary" onClick={async () => {
              try { await api.logout(session.csrfToken); expire(); }
              catch (e) { if (e instanceof ApiError && e.status === 401) expire(); else setError((e as Error).message); }
            }}>Sign out</button>
          </div>
        )}
      </header>
      {session?.mode === "mock" && (
        <div className="mock-banner" role="status">
          <strong>Prototype · Mock mode</strong>
          <span>
            AI responses are simulated. No real payments or purchases.
          </span>
        </div>
      )}
      {session && (
        <nav aria-label="Main navigation">
          {session.user.role === "Owner" ? (
            Object.entries(ownerPages).map(([key, value]) => (
              <a
                key={key}
                href={`#/owner/${key}`}
                aria-current={route === `/owner/${key}` ? "page" : undefined}
              >
                {value.title}
              </a>
            ))
          ) : (
            <a
              href="#/customer"
              aria-current={route === "/customer" ? "page" : undefined}
            >
              Requests & orders
            </a>
          )}
        </nav>
      )}
      <main id="main-content" tabIndex={-1}>
        {session && error && <Notice error>{error}</Notice>}
        {loading ? (
          <Notice>Opening your bakery workspace…</Notice>
        ) : !session ? (
          signedOut ? <SignIn onSuccess={() => { location.hash = "/"; setAttempt(value => value + 1); }} /> : (
            <section className="card access"><h1>We couldn’t open your workspace</h1>
              <Notice error>{error}</Notice>
              <button className="secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button>
            </section>
          )
        ) : !permitted ? (
          <section className="card access">
            <h1>Page unavailable</h1>
            <p>This page is not available for your account.</p>
            <a className="primary" href={`#${home}`}>
              Back to your workspace
            </a>
          </section>
        ) : session.user.role === "Customer" ? (
          <CustomerPage session={session} onExpired={expire} />
        ) : (
          <OwnerPage page={ownerPage} session={session} onExpired={expire} />
        )}
      </main>
      <footer>
        <span>BakeFlow</span>
        <span>A little less admin. A little more baking.</span>
        <span>ELEC5620 · Prototype</span>
      </footer>
    </>
  );
}
