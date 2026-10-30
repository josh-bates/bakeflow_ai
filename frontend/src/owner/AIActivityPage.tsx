import { useEffect, useState } from "react";
import { api, ApiError, type AIRequestRecord } from "../api/client";
import { Notice } from "../components/Notice";

// BAKE-37 (architecture [R5]): every AI operation the app ran, newest first,
// with its attempts, time taken, model and outcome. Read only; no prompt or
// reply text is ever saved.

const STATUS: Record<AIRequestRecord["status"], string> = {
  Running: "Running",
  Succeeded: "Succeeded",
  Failed: "Failed after retries",
  Interrupted: "Interrupted",
};
const RESULT: Record<string, string> = { usable: "usable", rejected: "failed the checks", error: "error" };

const when = (iso: string) => new Date(iso).toLocaleString();
const seconds = (ms: number | null) => (ms === null ? "—" : `${(ms / 1000).toFixed(1)} s`);
const model = (r: AIRequestRecord) => (r.provider === "mock" ? "Mock" : `${r.model} (${r.provider})`);
const attempts = (r: AIRequestRecord) =>
  r.outcome.map((a) => `${a.attempt}: ${RESULT[a.result] ?? a.result}${a.error ? ` (${a.error})` : ""}`).join("; ");

export function AIActivityPage() {
  const [requests, setRequests] = useState<AIRequestRecord[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.aiRequests().then(setRequests, (err: unknown) =>
      setError(err instanceof ApiError ? err.message : "AI activity could not be loaded."),
    );
  }, []);

  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>AI activity</h1>
        <p>Every assistant request, with its retries and result.</p>
      </section>
      {error && <Notice error>{error}</Notice>}
      {!requests && !error && <Notice>Loading AI activity…</Notice>}
      {requests && (
        <section className="card orders" aria-labelledby="ai-title">
          <h2 id="ai-title">Latest requests</h2>
          <p className="field-help">
            Each request gets at most three attempts. Only the task, timing, model and errors are kept, never what was
            asked or answered.
          </p>
          {requests.length === 0 ? (
            <p className="field-help">No AI requests yet.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <caption className="sr-only">AI requests</caption>
                <thead>
                  <tr>
                    <th scope="col">Task</th>
                    <th scope="col">Started</th>
                    <th scope="col">Result</th>
                    <th scope="col">Attempts</th>
                    <th scope="col">Time</th>
                    <th scope="col">Model</th>
                    <th scope="col">Prompt version</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map((r) => (
                    <tr key={r.id}>
                      <th scope="row">{r.task}</th>
                      <td>{when(r.startedAt)}</td>
                      <td>
                        {STATUS[r.status]}
                        {r.lastError && r.status !== "Succeeded" && <div className="field-help">{r.lastError}</div>}
                      </td>
                      <td title={attempts(r)}>{r.attempts}</td>
                      <td>{seconds(r.latencyMs)}</td>
                      <td>{model(r)}</td>
                      <td>{r.promptVersion ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </>
  );
}
