import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const line = (income: string, expenses: string, profit: string, margin: string | null, transactions: number) =>
  ({ income, expenses, profit, margin, transactions });
const profitability = {
  overall: line("94.23", "64.96", "29.27", "0.3106", 3),
  general: line("0.00", "50.00", "-50.00", null, 1),
  byOrder: [{ orderId: "1b2c3d4e-0000-0000-0000-000000000000", ...line("94.23", "14.96", "79.27", "0.8412", 2) }],
  byProduct: [{ productId: "p1", name: "Vanilla cupcake", ...line("94.23", "14.96", "79.27", "0.8412", 2) }],
  periodStart: null,
  periodEnd: null,
};
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  location.hash = "#/owner/finance";
  fetchMock = vi.fn().mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : url === "/api/finance/profitability"
        ? response(profitability)
        : response({}, 404),
  );
  vi.stubGlobal("fetch", fetchMock);
});

it("shows the owner the server's profit figures exactly, with the general-cost caveat", async () => {
  render(<App />);
  const overall = await screen.findByRole("row", { name: /All transactions/ });
  expect(within(overall).getByText("$94.23")).toBeInTheDocument();
  expect(within(overall).getByText("$29.27")).toBeInTheDocument();
  expect(within(overall).getByText("31.1%")).toBeInTheDocument();
  const general = screen.getByRole("row", { name: /General/ });
  expect(within(general).getByText("−$50.00")).toBeInTheDocument();
  expect(within(general).getByText("—")).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /Vanilla cupcake/ })).toHaveTextContent("$79.27");
  expect(screen.getByRole("row", { name: /Order 1b2c3d4e/ })).toHaveTextContent("84.1%");
  expect(screen.getByText(/per-order and per-product margins can look higher/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Finance" })).toHaveAttribute("aria-current", "page");
});

it("refuses a response with float money instead of decimal strings", async () => {
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : response({ ...profitability, overall: { ...profitability.overall, profit: 29.27 } }),
  );
  render(<App />);
  expect(await screen.findByText(/returned incomplete data/)).toBeInTheDocument();
  expect(screen.queryByText("$29.27")).not.toBeInTheDocument();
});

it("explains the figures only when asked, and keeps them when the explanation fails", async () => {
  let reply: Record<string, unknown> = { ...profitability, explanation: "Profit is $29.27.", notice: null };
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : url === "/api/finance/profitability"
        ? response(profitability)
        : url === "/api/finance/profitability/explanation" && init?.method === "POST"
          ? response(reply)
          : response({}, 404),
  );
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("row", { name: /All transactions/ });
  // No model call until the owner asks.
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/explanation"))).toBe(false);

  await user.click(screen.getByRole("button", { name: "Explain these figures" }));
  expect(await screen.findByText("Profit is $29.27.")).toBeInTheDocument();
  const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/explanation"))!;
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");

  reply = { ...profitability, explanation: null, notice: "Explanation unavailable. The figures above are unchanged." };
  await user.click(screen.getByRole("button", { name: "Explain these figures" }));
  expect(await screen.findByText(/Explanation unavailable/)).toBeInTheDocument();
  expect(screen.queryByText("Profit is $29.27.")).not.toBeInTheDocument();
  expect(within(screen.getByRole("row", { name: /All transactions/ })).getByText("$29.27")).toBeInTheDocument();
});
