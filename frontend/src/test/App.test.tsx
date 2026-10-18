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
  vi.stubGlobal("fetch", fetchMock);
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

it("submits text to the backend with CSRF and reports receipt without claiming a confirmed order", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response(session())
      : url === "/api/order-requests"
        ? response(
            {
              requestId: "REQ-1",
              message: "Please review the details with the bakery.",
            },
            202,
          )
        : response({ orders: [] }),
  );
  render(<App />);
  await user.type(
    await screen.findByLabelText("What would you like to order?"),
    "12 vanilla cupcakes",
  );
  await user.click(screen.getByRole("button", { name: /Send order request/ }));
  expect(
    await screen.findByText("Request received · REQ-1"),
  ).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/order-requests",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ message: "12 vanilla cupcakes" }),
      headers: expect.objectContaining({
        "X-CSRF-Token": "test-csrf",
        "Idempotency-Key": expect.any(String),
      }),
    }),
  );
  expect(screen.getByLabelText("What would you like to order?")).toHaveValue(
    "",
  );
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
    "Receipt was not confirmed",
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
  expect(screen.queryByText("No saved orders yet")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Refresh orders" }));
  expect(await screen.findByText("No saved orders yet")).toBeInTheDocument();
});

it("requires sign-in rather than inventing a customer role", async () => {
  fetchMock.mockResolvedValue(response({}, 401));
  render(<App />);
  expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute(
    "href",
    "/api/auth/login",
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
});

it("clears customer content when an order request reports an expired session", async () => {
  fetchMock
    .mockResolvedValueOnce(response(session()))
    .mockResolvedValueOnce(response({}, 401));
  render(<App />);
  expect(
    await screen.findByRole("link", { name: "Sign in" }),
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

it("lets an owner navigate all four clearly labelled placeholder pages", async () => {
  const user = userEvent.setup();
  fetchMock.mockResolvedValue(response(session("Owner")));
  render(<App />);
  await screen.findByRole("heading", { name: "Pending approvals" });
  for (const title of [
    "Orders",
    "Inventory",
    "Schedules",
    "Pending approvals",
  ]) {
    await user.click(screen.getByRole("link", { name: title }));
    expect(
      await screen.findByRole("heading", { name: title }),
    ).toBeInTheDocument();
    expect(screen.getByText(/This page is a placeholder/)).toBeInTheDocument();
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
  await screen.findByText("No saved orders yet");
  expect(screen.queryByText("Prototype · Mock mode")).not.toBeInTheDocument();
});
