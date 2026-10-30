import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

const session = (role = "Customer") => ({
  user: { displayName: "Zoe", role },
  mode: "mock",
  csrfToken: "test-csrf",
});
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  location.hash = "";
  fetchMock = vi
    .fn()
    .mockImplementation(async (url: string) =>
      url === "/api/session" ? response(session()) : response({ orders: [] }),
    );
  vi.stubGlobal("fetch", (url: string, options?: RequestInit) => {
    if (url === "/api/exception-requests") return Promise.resolve(response({ requests: [] }));
    if (url === "/api/owner/exception-catalogue") return Promise.resolve(response({ products: [] }));
    return fetchMock(url, options);
  });
});

it("loads only the signed-in customer’s orders and never supplies a customer identity", async () => {
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response(session())
      : response({
          orders: [
            {
              id: "ORD-12",
              summary: "12 vanilla cupcakes",
              status: "Draft",
              requiredAt: "2026-10-25T03:00:00Z",
            },
          ],
        }),
  );
  render(<App />);
  expect(await screen.findByText("ORD-12")).toBeInTheDocument();
  expect(screen.getByText("Draft")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Inventory" }),
  ).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/orders/mine",
    expect.objectContaining({ credentials: "same-origin", cache: "no-store" }),
  );
});

const preview = {
  summary: { items: [{ product: "Vanilla cupcake", quantity: 24, flavour: "vanilla", theme: "birthday", customisation: null }], required_at: "2027-10-02T12:00:00+10:00", collection_kind: "before", allergen_concerns: [], budget: null },
  issues: [], confirmationToken: "signed-summary",
};

it("shows a summary without saving until the customer confirms", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session" ? response(session()) :
    url === "/api/order-requests" ? response(preview) :
    url === "/api/order-requests/confirm" ? response({ orderId: "ORD-1", status: "Draft" }) : response({ orders: [] }));
  render(<App />);
  await user.type(await screen.findByLabelText("What would you like to order?"), "24 vanilla cupcakes");
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  expect(await screen.findByText("Your order summary")).toBeInTheDocument();
  expect(screen.getByText("No concerns stated by you")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/order-requests/confirm")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Confirm and save draft" }));
  expect(await screen.findByText("Draft saved · ORD-1")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/order-requests/confirm", expect.objectContaining({
    body: JSON.stringify({ confirmationToken: "signed-summary" }),
    headers: expect.objectContaining({ "X-CSRF-Token": "test-csrf" }),
  }));
});

it("blocks incomplete summaries and lets the customer correct the request", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) => url === "/api/session" ? response(session()) :
    url === "/api/order-requests" ? response({ ...preview, issues: ["Please provide allergen information."], confirmationToken: null }) : response({ orders: [] }));
  render(<App />);
  await user.type(await screen.findByLabelText("What would you like to order?"), "24 cupcakes");
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  expect(await screen.findByText("Please provide allergen information.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm and save draft" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Correct my request" }));
  expect(screen.queryByText("Your order summary")).not.toBeInTheDocument();
  expect(screen.getByLabelText("What would you like to order?")).toHaveValue("24 cupcakes");
});

it("retains a summary after save failure and retries the same confirmation", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) => url === "/api/session" ? response(session()) :
    url === "/api/order-requests" ? response(preview) :
    url === "/api/order-requests/confirm" ? response({}, 503) : response({ orders: [] }));
  render(<App />);
  await user.type(await screen.findByLabelText("What would you like to order?"), "24 cupcakes");
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  await user.click(await screen.findByRole("button", { name: "Confirm and save draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Saving has not been confirmed");
  expect(screen.queryByText(/Draft saved/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Confirm and save draft" }));
  const calls = fetchMock.mock.calls.filter(([url]) => url === "/api/order-requests/confirm");
  expect(calls).toHaveLength(2);
  expect(calls[0][1].body).toBe(calls[1][1].body);
});

it("retains text and the same idempotency key after a failed submission", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response(session())
      : url === "/api/order-requests"
        ? response({}, 500)
        : response({ orders: [] }),
  );
  render(<App />);
  await user.type(
    await screen.findByLabelText("What would you like to order?"),
    "Birthday cake",
  );
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "No draft was saved",
  );
  expect(screen.getByLabelText("What would you like to order?")).toHaveValue(
    "Birthday cake",
  );
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([url]) => url === "/api/order-requests"),
    ).toHaveLength(2),
  );
  const posts = fetchMock.mock.calls.filter(
    ([url]) => url === "/api/order-requests",
  );
  expect(posts[0][1].headers["Idempotency-Key"]).toBe(
    posts[1][1].headers["Idempotency-Key"],
  );
  expect(screen.queryByText(/Request received/)).not.toBeInTheDocument();
});

it("disables submission while a response is pending", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response(session())
      : url === "/api/order-requests"
        ? new Promise(() => {})
        : response({ orders: [] }),
  );
  render(<App />);
  await user.type(
    await screen.findByLabelText("What would you like to order?"),
    "Cake",
  );
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  expect(
    screen.getByRole("button", { name: /Sending request/ }),
  ).toBeDisabled();
});

it("distinguishes unavailable order data from an empty list and supports retry", async () => {
  const user = userEvent.setup();
  fetchMock
    .mockResolvedValueOnce(response(session()))
    .mockResolvedValueOnce(response({}, 503));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "could not complete",
  );
  expect(screen.queryByText("No orders yet")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Refresh orders" }));
  expect(await screen.findByText("No orders yet")).toBeInTheDocument();
});

it("requires sign-in rather than inventing a customer role", async () => {
  fetchMock.mockResolvedValue(response({}, 401));
  render(<App />);
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
});

it("clears customer content when an order request reports an expired session", async () => {
  fetchMock
    .mockResolvedValueOnce(response(session()))
    .mockResolvedValueOnce(response({}, 401));
  render(<App />);
  expect(
    await screen.findByRole("button", { name: "Sign in" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByLabelText("What would you like to order?"),
  ).not.toBeInTheDocument();
});

it("blocks a customer from rendering an owner route", async () => {
  location.hash = "#/owner/inventory";
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Page unavailable" }),
  ).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("lets an owner navigate orders and the exception review dashboard", async () => {
  const user = userEvent.setup();
  fetchMock.mockResolvedValue(response(session("Owner")));
  render(<App />);
  await screen.findByRole("heading", { name: "Pending approvals" });
  for (const title of [
    "Orders",
    "Pending approvals",
  ]) {
    await user.click(screen.getByRole("link", { name: title }));
    expect(
      await screen.findByRole("heading", { name: title }),
    ).toBeInTheDocument();
    if (title === "Orders") expect(screen.getByText(/This page is a placeholder/)).toBeInTheDocument();
    else expect(screen.getByRole("heading", { name: "Order exceptions awaiting review" })).toBeInTheDocument();
  }
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("rejects a malformed successful response rather than showing fabricated orders", async () => {
  fetchMock
    .mockResolvedValueOnce(response(session()))
    .mockResolvedValueOnce(response({ orders: [{ id: "bad" }] }));
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("incomplete data");
});

it("does not display a mock banner for a live session", async () => {
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ ...session(), mode: "live" })
      : response({ orders: [] }),
  );
  render(<App />);
  await screen.findByText("No orders yet");
  expect(screen.queryByText("Prototype · Mock mode")).not.toBeInTheDocument();
});

it('homepage shortcuts take the customer to the request form and their orders', async () => {
  const user = userEvent.setup();
  const scroll = vi.fn();
  vi.stubGlobal('scrollTo', vi.fn());
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  render(<App />);
  await user.click(await screen.findByRole('button', { name: /Start a custom order/ }));
  expect(screen.getByLabelText('What would you like to order?')).toHaveFocus();
  await user.click(screen.getByRole('button', { name: /View my orders/ }));
  expect(screen.getByRole('heading', { name: 'Your orders' })).toHaveFocus();
  expect(scroll).toHaveBeenCalledTimes(2);
});

it("signs in with credentials and signs out without retaining customer content", async () => {
  const user = userEvent.setup();
  let loggedIn = false;
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/auth/login") { loggedIn = true; return new Response(null, { status: 204 }); }
    if (url === "/api/auth/logout") { loggedIn = false; return new Response(null, { status: 204 }); }
    return url === "/api/session" ? response(loggedIn ? session() : {}, loggedIn ? 200 : 401) : response({ orders: [] });
  });
  render(<App />);
  await user.type(await screen.findByLabelText("Email"), "customer@bakeflow.test");
  await user.type(screen.getByLabelText("Password"), "test-password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("No orders yet")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({
    body: JSON.stringify({ email: "customer@bakeflow.test", password: "test-password" }),
    headers: expect.objectContaining({ "X-Requested-With": "BakeFlow" }),
  }));
  await user.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByLabelText("Password")).toHaveValue("");
  expect(screen.queryByText("No orders yet")).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({
    headers: expect.objectContaining({ "X-CSRF-Token": "test-csrf" }),
  }));
});

it("reports rejected credentials and clears the password", async () => {
  const user = userEvent.setup();
  fetchMock.mockResolvedValue(response({}, 401));
  render(<App />);
  await user.type(await screen.findByLabelText("Email"), "customer@bakeflow.test");
  await user.type(screen.getByLabelText("Password"), "incorrect-password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  expect(screen.getByLabelText("Password")).toHaveValue("");
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
});

it("shows the stored status even when its explanation is unavailable", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) => url === "/api/session" ? response(session()) :
    url === "/api/orders/ORD-18/tracking" ? response({ id: "ORD-18", status: "Draft", version: 1, requiredAt: "2027-10-02T02:00:00Z", explanationAvailable: false, explanation: "Explanation unavailable. Your recorded order status is shown above. Please try again later." }) :
    response({ orders: [{ id: "ORD-18", status: "Draft", summary: "24 cupcakes", requiredAt: "2027-10-02T02:00:00Z" }] }));
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Track order ORD-18" }));
  expect(await screen.findByText("Recorded status: Draft")).toBeInTheDocument();
  expect(screen.getByText(/Explanation unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh status" })).toBeEnabled();
});

it("refreshes tracking from the server rather than keeping the old stage", async () => {
  const user = userEvent.setup();
  let reads = 0;
  fetchMock.mockImplementation(async (url: string) => url === "/api/session" ? response(session()) :
    url === "/api/orders/ORD-18/tracking" ? response({ id: "ORD-18", status: ++reads === 1 ? "Draft" : "Quoted", version: reads, requiredAt: "2027-10-02T02:00:00Z", explanationAvailable: true, explanation: reads === 1 ? "Wait for quotation." : "Review your quotation." }) :
    response({ orders: [{ id: "ORD-18", status: "Draft", summary: "24 cupcakes", requiredAt: "2027-10-02T02:00:00Z" }] }));
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Track order ORD-18" }));
  await screen.findByText("Recorded status: Draft");
  await user.click(screen.getByRole("button", { name: "Refresh status" }));
  expect(await screen.findByText("Recorded status: Quoted")).toBeInTheDocument();
  expect(screen.queryByText("Wait for quotation.")).not.toBeInTheDocument();
});
