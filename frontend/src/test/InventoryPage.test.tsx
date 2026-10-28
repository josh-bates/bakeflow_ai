import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const FLOUR = "11111111-0000-0000-0000-000000000000";
const SUGAR = "22222222-0000-0000-0000-000000000000";
const stock = (flourOnHand: string, flourAvailable: string) => ({
  items: [
    { id: SUGAR, ingredient: "Caster sugar", unit: "g", onHand: "2000.000", committed: "0.000", available: "2000.000" },
    { id: FLOUR, ingredient: "Flour", unit: "g", onHand: flourOnHand, committed: "600.000", available: flourAvailable },
  ],
});
const receipt = (replayed: boolean) => ({
  id: "r1",
  itemId: FLOUR,
  ingredient: "Flour",
  unit: "g",
  quantity: "2500.000",
  sourceType: "Invoice",
  sourceReference: "INV-1001",
  receivedAt: "2026-10-27T01:00:00+00:00",
  onHand: "2900.000",
  replayed,
});
let current: ReturnType<typeof stock>;
let posted: (() => Response) | null;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  location.hash = "#/owner/inventory";
  current = stock("400.000", "-200.000");
  posted = () => {
    current = stock("2900.000", "2300.000");
    return response(receipt(false), 201);
  };
  fetchMock = vi.fn().mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : url === "/api/inventory/items"
        ? response(current)
        : url === "/api/inventory/receipts" && posted
          ? posted()
          : response({}, 404),
  );
  vi.stubGlobal("fetch", fetchMock);
});

const receiptCalls = () => fetchMock.mock.calls.filter(([url]) => url === "/api/inventory/receipts");

async function fillReceipt(quantity: string, reference = "inv-1001") {
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("Ingredient"), "Flour (g)");
  await user.type(screen.getByLabelText(/Quantity received/), quantity);
  await user.type(screen.getByLabelText("Reference number"), reference);
  return user;
}

it("shows on hand, committed and available stock exactly as the server gives it", async () => {
  render(<App />);
  const flour = await screen.findByRole("row", { name: /Flour/ });
  expect(within(flour).getByText("400.000 g")).toBeInTheDocument();
  expect(within(flour).getByText("600.000 g")).toBeInTheDocument();
  expect(within(flour).getByText("−200.000 g")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Inventory" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByText(/This page is a placeholder/)).not.toBeInTheDocument();
});

it("records a receipt with the CSRF token and shows the raised stock", async () => {
  render(<App />);
  const user = await fillReceipt("2500");
  expect(screen.getByLabelText("Quantity received (g)")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Record receipt" }));

  expect(
    await screen.findByText(
      "Recorded 2500.000 g of Flour from Invoice INV-1001. Flour on hand is now 2900.000 g.",
    ),
  ).toBeInTheDocument();
  const [[, init]] = receiptCalls();
  expect(JSON.parse(init.body)).toEqual({
    itemId: FLOUR,
    quantity: "2500",
    sourceType: "Invoice",
    sourceReference: "inv-1001",
  });
  expect(init.headers).toEqual(expect.objectContaining({ "X-CSRF-Token": "t" }));
  const flour = await screen.findByRole("row", { name: /Flour/ });
  expect(await within(flour).findByText("2900.000 g")).toBeInTheDocument();
  expect(screen.getByLabelText("Reference number")).toHaveValue("");
});

it("tells the owner when a repeated receipt did not raise stock again", async () => {
  posted = () => response(receipt(true));
  render(<App />);
  const user = await fillReceipt("2500");
  await user.selectOptions(screen.getByLabelText("Source"), "Invoice");
  await user.click(screen.getByRole("button", { name: "Record receipt" }));
  expect(
    await screen.findByText(/Invoice INV-1001 was already recorded for Flour \(2500.000 g\)\. Stock was not raised again\./),
  ).toBeInTheDocument();
});

it("shows the server's reason when the reference already recorded another quantity", async () => {
  posted = () =>
    response(
      { detail: "Invoice INV-1001 already recorded 2500.000 g of Flour. Stock was not changed.", existing: receipt(false) },
      409,
    );
  render(<App />);
  const user = await fillReceipt("2400");
  await user.click(screen.getByRole("button", { name: "Record receipt" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Invoice INV-1001 already recorded 2500.000 g of Flour. Stock was not changed.",
  );
});

it.each(["0", "0.000", "2.5005", "-3", "1e3", "2,5"])("does not send a quantity of %s", async (quantity) => {
  render(<App />);
  await fillReceipt(quantity);
  expect(screen.getByRole("button", { name: "Record receipt" })).toBeDisabled();
  expect(receiptCalls()).toHaveLength(0);
});

it("refuses stock levels sent as numbers instead of decimal strings", async () => {
  current = { items: [{ ...stock("400.000", "-200.000").items[1], onHand: 400 as unknown as string }] };
  render(<App />);
  expect(await screen.findByText(/returned incomplete data/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Record receipt" })).not.toBeInTheDocument();
});
