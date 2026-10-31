import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

// BAKE-23: the owner prices an order, reviews the quote and approves one
// version; the customer accepts or declines exactly that version.

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const ORDER = "aaaaaaaa-0000-0000-0000-000000000000";
const QUOTE = "bbbbbbbb-0000-0000-0000-000000000000";
const quote = (version = 1, extra = "0.00", total = "94.23") => ({
  id: QUOTE,
  version,
  orderId: ORDER,
  materialCost: "14.96",
  labourCost: "45.00",
  packagingCost: "6.00",
  additionalCost: extra,
  targetMargin: "0.3000",
  total,
  customerDecision: "Pending",
  requiresManualReview: true,
  explanation: null,
});
const item = (q: ReturnType<typeof quote> | null, approval: string | null) => ({
  orderId: ORDER,
  summary: "24 Vanilla cupcake",
  requiredAt: "2026-11-05T01:00:00+00:00",
  orderStatus: q ? "Quoted" : "Draft",
  ready: true,
  approval,
  quote: q,
});

let queue: unknown[];
let routes: Record<string, () => Response>;
let fetchMock: ReturnType<typeof vi.fn>;
const session = (role: "Owner" | "Customer") =>
  response({ user: { displayName: role, role }, mode: "mock", csrfToken: "t" });

function setup(role: "Owner" | "Customer") {
  fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/session") return session(role);
    if (url === "/api/finance/quotes") return response({ orders: queue });
    const route = routes[`${init?.method ?? "GET"} ${url}`];
    return route ? route() : response({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}
const calls = (method: string, url: string) =>
  fetchMock.mock.calls.filter(([u, init]) => u === url && (init?.method ?? "GET") === method);

beforeEach(() => {
  location.hash = "#/owner/approvals";
  queue = [item(null, null)];
  routes = {};
});

it("prices an order and shows every figure with the manual-review flag", async () => {
  setup("Owner");
  routes[`POST /api/finance/orders/${ORDER}/quotes`] = () => {
    queue = [item(quote(), "Pending")];
    return response(quote(), 201);
  };
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "Calculate quote" }));

  expect(await screen.findByText(/version 1 is ready for your review/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Version 1 · Awaiting your approval/ })).toBeInTheDocument();
  expect(screen.getByText("$94.23")).toBeInTheDocument();
  expect(screen.getByText(/target margin 30.0%/)).toBeInTheDocument();
  expect(screen.getByText(/Flagged for manual review/)).toBeInTheDocument();
  expect(screen.getByText(/Explanation unavailable/)).toBeInTheDocument();
  const [, init] = calls("POST", `/api/finance/orders/${ORDER}/quotes`)[0];
  expect(JSON.parse(String(init.body))).toEqual({ additionalCosts: [] });
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");
});

it("approves exactly the shown version", async () => {
  setup("Owner");
  queue = [item(quote(), "Pending")];
  const url = `/api/finance/quotes/${QUOTE}/versions/1/approve`;
  routes[`POST ${url}`] = () => {
    queue = [item(quote(), "Approved")];
    return response(quote());
  };
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "Approve version 1" }));

  expect(await screen.findByText(/approved and shown to the customer/)).toBeInTheDocument();
  expect(screen.getByText("Customer has not decided")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  expect(calls("POST", url)).toHaveLength(1);
});

it("revises with an extra cost, and never sends an invalid amount", async () => {
  setup("Owner");
  queue = [item(quote(), "Withheld")];
  const url = `/api/finance/quotes/${QUOTE}/versions/1/revise`;
  routes[`POST ${url}`] = () => {
    queue = [item(quote(2, "5.00", "101.37"), "Pending")];
    return response(quote(2, "5.00", "101.37"), 201);
  };
  const user = userEvent.setup();
  render(<App />);

  expect(await screen.findByRole("heading", { name: /Withheld · revise it/ })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Extra cost (optional)"), "Gift box");
  await user.type(screen.getByLabelText("Amount (AUD)"), "5.001");
  const revise = screen.getByRole("button", { name: "Revise as a new version" });
  expect(revise).toBeDisabled();

  await user.clear(screen.getByLabelText("Amount (AUD)"));
  await user.type(screen.getByLabelText("Amount (AUD)"), "5.00");
  await user.click(revise);

  expect(await screen.findByText(/version 2 is ready for your review/)).toBeInTheDocument();
  expect(JSON.parse(String(calls("POST", url)[0][1].body))).toEqual({
    additionalCosts: [{ description: "Gift box", amount: "5.00" }],
  });
});

it("shows the server's reason when a decision is refused", async () => {
  setup("Owner");
  queue = [item(quote(), "Pending")];
  routes[`POST /api/finance/quotes/${QUOTE}/versions/1/approve`] = () =>
    response({ detail: "quote is at version 2" }, 409);
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: "Approve version 1" }));

  expect(await screen.findByText("quote is at version 2")).toHaveAttribute("role", "alert");
});

it("lets the customer accept the approved quote for a quoted order", async () => {
  location.hash = "#/customer";
  setup("Customer");
  let status = "Quoted";
  routes["GET /api/orders/mine"] = () =>
    response({ orders: [{ id: ORDER, summary: "24 Vanilla cupcake", status, requiredAt: "2026-11-05T01:00:00+00:00" }] });
  routes[`GET /api/orders/${ORDER}/quote`] = () =>
    response({ quote: { id: QUOTE, version: 1, orderId: ORDER, total: "94.23", customerDecision: "Pending" } });
  routes[`POST /api/quotes/${QUOTE}/versions/1/accept`] = () => {
    status = "Confirmed";
    return response({ orderId: ORDER, replayed: false });
  };
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: `View quote for order ${ORDER}` }));
  const card = await screen.findByRole("region", { name: /Quote for order aaaaaaaa/ });
  expect(within(card).getByText(/Total price: \$94.23/)).toBeInTheDocument();
  // The customer never sees the bakery's costs.
  expect(within(card).queryByText(/14.96|Labour|Ingredients/)).not.toBeInTheDocument();

  await user.click(within(card).getByRole("button", { name: "Accept quote" }));

  expect(await screen.findByText(/Your order is confirmed/)).toBeInTheDocument();
  expect(await screen.findByText("Confirmed")).toBeInTheDocument();
  expect(calls("POST", `/api/quotes/${QUOTE}/versions/1/accept`)).toHaveLength(1);
});

it("tells the customer when no quote is ready yet", async () => {
  location.hash = "#/customer";
  setup("Customer");
  routes["GET /api/orders/mine"] = () =>
    response({ orders: [{ id: ORDER, summary: "24 Vanilla cupcake", status: "Quoted", requiredAt: "2026-11-05T01:00:00+00:00" }] });
  routes[`GET /api/orders/${ORDER}/quote`] = () => response({ quote: null });
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("button", { name: `View quote for order ${ORDER}` }));

  expect(await screen.findByText(/still preparing your quote/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Accept quote" })).not.toBeInTheDocument();
});
