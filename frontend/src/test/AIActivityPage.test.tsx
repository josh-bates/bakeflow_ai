import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const request = (task: string, status: string, changes: object = {}) => ({
  id: `${task}-id`,
  task,
  subject: { recordType: "LowStockAlerts", recordId: "5c1d2e3f-0000-0000-0000-000000000000", version: 1 },
  actor: "owner-id",
  provider: "mock",
  model: "mock",
  promptVersion: "explain_low_stock/1",
  status,
  attempts: 1,
  latencyMs: 1234,
  lastError: null,
  outcome: [{ attempt: 1, latencyMs: 1234, result: "usable" }],
  startedAt: "2026-10-29T09:00:00+00:00",
  finishedAt: "2026-10-29T09:00:01+00:00",
  ...changes,
});
let requests: unknown;
beforeEach(() => {
  location.hash = "#/owner/ai";
  requests = {
    requests: [
      request("explainLowStock", "Succeeded"),
      request("proposeSchedule", "Failed", {
        provider: "api.example.test",
        model: "example-model",
        promptVersion: "propose_schedule/1",
        attempts: 3,
        lastError: "the reply did not pass the checks",
        outcome: [1, 2, 3].map((attempt) => ({ attempt, latencyMs: 900, result: "rejected" })),
      }),
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) =>
      url === "/api/session"
        ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
        : url === "/api/owner/ai-requests"
          ? response(requests)
          : response({}, 404),
    ),
  );
});

it("lists each AI request with its result, attempts, time and model", async () => {
  render(<App />);
  const table = within(await screen.findByRole("table", { name: "AI requests" }));

  const explained = table.getByRole("row", { name: /explainLowStock/ });
  expect(explained).toHaveTextContent("Succeeded");
  expect(explained).toHaveTextContent("1.2 s");
  expect(explained).toHaveTextContent("Mock");
  expect(explained).toHaveTextContent("explain_low_stock/1");
  const planned = table.getByRole("row", { name: /proposeSchedule/ });
  expect(planned).toHaveTextContent("Failed after retries");
  expect(planned).toHaveTextContent("the reply did not pass the checks");
  expect(planned).toHaveTextContent("example-model (api.example.test)");
  expect(within(planned).getByText("3")).toHaveAttribute("title", expect.stringContaining("3: failed the checks"));
  expect(screen.getByRole("link", { name: "AI activity" })).toHaveAttribute("aria-current", "page");
});

it("refuses a malformed list rather than showing made-up activity", async () => {
  requests = { requests: [{ ...request("explainLowStock", "Done") }] };
  render(<App />);
  expect(await screen.findByText(/returned incomplete data/)).toBeInTheDocument();
});
