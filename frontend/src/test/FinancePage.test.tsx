import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
afterEach(() => {
  vi.useRealTimers();
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

// BAKE-43 (UC-06 steps 1-4): the owner records a transaction through BAKE-24's API.
const ORDER = "1b2c3d4e-0000-0000-0000-000000000000";
function ledger(reply: () => Response) {
  let loads = 0;
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : url === "/api/finance/profitability"
        ? response(++loads === 1 ? profitability : { ...profitability, overall: line("94.23", "114.96", "-20.73", "-0.2200", 4) })
        : url === "/api/finance/transactions" && init?.method === "POST"
          ? reply()
          : response({}, 404),
  );
}
const posted = () =>
  fetchMock.mock.calls.filter(([url, init]) => url === "/api/finance/transactions" && init?.method === "POST");
async function fill(user: ReturnType<typeof userEvent.setup>, amount: string, reference = "INV-1042") {
  await screen.findByRole("row", { name: /All transactions/ });
  if (amount) await user.type(screen.getByLabelText("Amount (AUD)"), amount);
  await user.type(screen.getByLabelText("Reference"), reference);
}

it("records an expense with exactly the entered values and reloads the figures", async () => {
  ledger(() => response({ id: "t1", kind: "Expense", amount: "50.00", sourceType: "Invoice",
    sourceReference: "INV-1042", occurredAt: "2026-10-30T13:00:00+00:00", orderId: ORDER, productId: null }, 201));
  const user = userEvent.setup();
  render(<App />);
  await fill(user, "50");
  await user.clear(screen.getByLabelText("Date"));
  await user.type(screen.getByLabelText("Date"), "2026-10-31");
  await user.selectOptions(screen.getByLabelText("Order (optional)"), "Order 1b2c3d4e");

  await user.click(screen.getByRole("button", { name: "Record transaction" }));

  expect(await screen.findByText("Expense Invoice INV-1042 of $50.00 recorded.")).toBeInTheDocument();
  const [, init] = posted()[0];
  expect(JSON.parse(String(init.body))).toEqual({
    kind: "Expense", amount: "50.00", sourceType: "Invoice", sourceReference: "INV-1042",
    occurredAt: new Date("2026-10-31T00:00:00").toISOString(), orderId: ORDER,
  });
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");
  // Step 4: the profitability table comes from the ledger again.
  expect(await within(await screen.findByRole("row", { name: /All transactions/ })).findByText("−$20.73"))
    .toBeInTheDocument();
  expect(screen.getByLabelText("Reference")).toHaveValue("");
});

it("shows the fields the server asks to fix, and nothing is reported as saved", async () => {
  ledger(() => response({ detail: "Some details need fixing. Nothing was recorded.",
    issues: [{ field: "occurred_at", message: "cannot be in the future" }] }, 422));
  const user = userEvent.setup();
  render(<App />);
  await fill(user, "12.5");

  await user.click(screen.getByRole("button", { name: "Record transaction" }));

  expect(await screen.findByText(/Some details need fixing/)).toBeInTheDocument();
  expect(screen.getByText("Date: cannot be in the future")).toBeInTheDocument();
  expect(screen.queryByText(/of \$12.50 recorded/)).not.toBeInTheDocument();
  expect(JSON.parse(String(posted()[0][1].body)).amount).toBe("12.50");
  expect(screen.getByLabelText("Reference")).toHaveValue("INV-1042");
});

it("reports a duplicate reference as already recorded", async () => {
  ledger(() => response({ detail: "Invoice INV-1042 is already recorded. Nothing was changed." }, 409));
  const user = userEvent.setup();
  render(<App />);
  await fill(user, "50.00");

  await user.click(screen.getByRole("button", { name: "Record transaction" }));

  expect(await screen.findByText("Invoice INV-1042 is already recorded. Nothing was changed.")).toBeInTheDocument();
  expect(screen.queryByText(/of \$50.00 recorded/)).not.toBeInTheDocument();
});

it("removes the last saved message when the next save fails", async () => {
  const replies = [
    response({ id: "t1", kind: "Expense", amount: "50.00", sourceType: "Invoice",
      sourceReference: "INV-1042", occurredAt: "2026-10-30T13:00:00+00:00", orderId: null, productId: null }, 201),
    response({ detail: "Invoice INV-1042 is already recorded. Nothing was changed." }, 409),
  ];
  ledger(() => replies.shift()!);
  const user = userEvent.setup();
  render(<App />);
  await fill(user, "50");
  await user.click(screen.getByRole("button", { name: "Record transaction" }));
  expect(await screen.findByText("Expense Invoice INV-1042 of $50.00 recorded.")).toBeInTheDocument();

  await fill(user, "50");
  await user.click(screen.getByRole("button", { name: "Record transaction" }));

  expect(await screen.findByText(/already recorded/)).toBeInTheDocument();
  expect(screen.queryByText(/of \$50.00 recorded/)).not.toBeInTheDocument();
});

it("ignores an explanation that was asked for before a save", async () => {
  let finishExplaining = (_: Response) => {};
  let loads = 0;
  fetchMock.mockImplementation((url: string, init?: RequestInit) =>
    url === "/api/finance/profitability/explanation"
      ? new Promise((resolve) => { finishExplaining = resolve; })
      : Promise.resolve(
          url === "/api/session"
            ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
            : url === "/api/finance/profitability"
              ? response(++loads === 1 ? profitability : { ...profitability, overall: line("94.23", "114.96", "-20.73", "-0.2200", 4) })
              : url === "/api/finance/transactions" && init?.method === "POST"
                ? response({ id: "t1", kind: "Expense", amount: "50.00", sourceType: "Invoice",
                    sourceReference: "INV-1042", occurredAt: "2026-10-30T13:00:00+00:00", orderId: null, productId: null }, 201)
                : response({}, 404),
        ),
  );
  const user = userEvent.setup();
  render(<App />);
  await fill(user, "50");
  await user.click(screen.getByRole("button", { name: "Explain these figures" }));
  await user.click(screen.getByRole("button", { name: "Record transaction" }));
  const overall = () => within(screen.getByRole("row", { name: /All transactions/ }));
  expect(await overall().findByText("−$20.73")).toBeInTheDocument();

  finishExplaining(response({ ...profitability, explanation: "Profit is $29.27.", notice: null }));

  expect(await screen.findByRole("button", { name: "Explain these figures" })).toBeEnabled();
  expect(screen.queryByText("Profit is $29.27.")).not.toBeInTheDocument();
  expect(overall().getByText("−$20.73")).toBeInTheDocument();
});

it("defaults the date to the day the form opens, not the day the app loaded", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2031, 2, 4, 12));
  render(<App />);
  await screen.findByRole("row", { name: /All transactions/ });
  expect(screen.getByLabelText("Date")).toHaveValue("2031-03-04");
});

it("never sends an invalid amount", async () => {
  ledger(() => response({}, 500));
  const user = userEvent.setup();
  render(<App />);
  await fill(user, "", "INV-1");
  const amount = screen.getByLabelText("Amount (AUD)");

  for (const value of ["5.001", "0", "-4", "1e3", "12,50"]) {
    await user.clear(amount);
    await user.type(amount, value);
    expect(screen.getByRole("button", { name: "Record transaction" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Record transaction" }));
  }
  expect(screen.getByText(/at most two decimals/)).toBeInTheDocument();
  expect(posted()).toHaveLength(0);
});
