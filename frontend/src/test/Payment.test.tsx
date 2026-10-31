import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

// BAKE-39: the customer pays a confirmed order in test mode. The amount comes
// from the server (the accepted quote); the page never sends one.

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const ORDER = "aaaaaaaa-0000-0000-0000-000000000000";
const PAYMENT = "cccccccc-0000-0000-0000-000000000000";
const payment = (status: string, extra: Record<string, unknown> = {}) => ({
  id: PAYMENT,
  orderId: ORDER,
  quoteId: "q",
  quoteVersion: 1,
  amount: "94.23",
  status,
  paid: status === "Succeeded",
  createdAt: "2026-10-30T01:00:00+00:00",
  processedAt: status === "Created" ? null : "2026-10-30T01:01:00+00:00",
  ...extra,
});

let routes: Record<string, () => Response>;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  location.hash = "#/customer";
  routes = {
    "GET /api/exception-requests": () => response({ requests: [] }),
    "GET /api/orders/mine": () =>
      response({ orders: [{ id: ORDER, summary: "24 Vanilla cupcake", status: "Confirmed", requiredAt: "2026-11-05T01:00:00+00:00" }] }),
    [`GET /api/orders/${ORDER}/payments`]: () => response({ payments: [] }),
    [`POST /api/orders/${ORDER}/payments`]: () => response(payment("Created"), 201),
  };
  fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/session")
      return response({ user: { displayName: "Customer", role: "Customer" }, mode: "mock", csrfToken: "t" });
    const route = routes[`${init?.method ?? "GET"} ${url}`];
    return route ? route() : response({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});
const checkout = `/api/payments/${PAYMENT}/mock-checkout`;
const posts = (url: string) => fetchMock.mock.calls.filter(([u, init]) => u === url && init?.method === "POST");

it("pays the confirmed order's quote total in test mode", async () => {
  routes[`POST ${checkout}`] = () => response(payment("Succeeded", { replayed: false }));
  const user = userEvent.setup();
  render(<App />);

  await screen.findByText("24 Vanilla cupcake", {}, { timeout: 5000 });
  await user.click(screen.getByRole("button", { name: `Pay for order ${ORDER}` }));
  const card = await screen.findByRole("region", { name: /Payment for order aaaaaaaa/ });
  expect(within(card).getByText(/Amount due: \$94.23/)).toBeInTheDocument();
  expect(within(card).getByText(/No card details are needed/)).toBeInTheDocument();

  await user.click(within(card).getByRole("button", { name: "Pay $94.23 (test)" }));

  expect(await screen.findByText("Payment of $94.23 received. Thank you!")).toBeInTheDocument();
  const [, init] = posts(checkout)[0];
  // Only the outcome is sent: never an amount or card details.
  expect(JSON.parse(String(init.body))).toEqual({ outcome: "Succeeded" });
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");
});

it("shows a declined payment clearly and lets the customer try again", async () => {
  routes[`POST ${checkout}`] = () => response(payment("Failed"));
  const user = userEvent.setup();
  render(<App />);

  await screen.findByText("24 Vanilla cupcake", {}, { timeout: 5000 });
  await user.click(screen.getByRole("button", { name: `Pay for order ${ORDER}` }));
  await user.click(await screen.findByRole("button", { name: "Simulate a declined payment" }));

  expect(await screen.findByText(/did not go through\. Nothing was charged\./)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: "Pay $94.23 (test)" })).toBeInTheDocument();
});

it("explains why a success could not be recorded", async () => {
  routes[`POST ${checkout}`] = () =>
    response(payment("Failed", { reason: "the order's quote is not accepted, current and approved" }));
  const user = userEvent.setup();
  render(<App />);

  await screen.findByText("24 Vanilla cupcake", {}, { timeout: 5000 });
  await user.click(screen.getByRole("button", { name: `Pay for order ${ORDER}` }));
  await user.click(await screen.findByRole("button", { name: "Pay $94.23 (test)" }));

  expect(await screen.findByText(/not accepted, current and approved\. Nothing was charged/)).toBeInTheDocument();
});

it("does not start a second payment for an order already paid", async () => {
  routes[`GET /api/orders/${ORDER}/payments`] = () => response({ payments: [payment("Succeeded")] });
  const user = userEvent.setup();
  render(<App />);

  await screen.findByText("24 Vanilla cupcake", {}, { timeout: 5000 });
  await user.click(screen.getByRole("button", { name: `Pay for order ${ORDER}` }));

  expect(await screen.findByText("Paid $94.23. Thank you!")).toBeInTheDocument();
  expect(posts(`/api/orders/${ORDER}/payments`)).toHaveLength(0);
});

it("offers no payment for an order that is not confirmed", async () => {
  routes["GET /api/orders/mine"] = () =>
    response({ orders: [{ id: ORDER, summary: "24 Vanilla cupcake", status: "Draft", requiredAt: "2026-11-05T01:00:00+00:00" }] });
  render(<App />);
  await screen.findByText("24 Vanilla cupcake");
  expect(screen.queryByRole("button", { name: `Pay for order ${ORDER}` })).not.toBeInTheDocument();
});
