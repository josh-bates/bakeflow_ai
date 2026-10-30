import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";
import { readableQuantity } from "../owner/InventoryPage";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const FLOUR = "11111111-0000-0000-0000-000000000000";
const SUGAR = "22222222-0000-0000-0000-000000000000";
const SUGAR_ITEM = {
  id: SUGAR, ingredient: "Caster sugar", unit: "g", onHand: "2000.000", committed: "0.000", available: "2000.000",
  reorderLevel: "300.000", version: 1, low: false, suggestedReorder: null as string | null,
};
const flourItem = (onHand: string, available: string) => {
  const low = Number(available) < 200;
  return {
    id: FLOUR, ingredient: "Flour", unit: "g", onHand, committed: "600.000", available,
    reorderLevel: "200.000", version: 3, low,
    suggestedReorder: low ? (400 - Number(available)).toFixed(3) : null,
  };
};
const stock = (flourOnHand: string, flourAvailable: string) => ({
  items: [SUGAR_ITEM, flourItem(flourOnHand, flourAvailable)],
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
let levelSaved: () => Response;
let explained: () => Response;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  location.hash = "#/owner/inventory";
  current = stock("400.000", "-200.000");
  posted = () => {
    current = stock("2900.000", "2300.000");
    return response(receipt(false), 201);
  };
  levelSaved = () => {
    const sugar = { ...SUGAR_ITEM, reorderLevel: "2500.000", version: 2, low: true, suggestedReorder: "3000.000" };
    current = { items: [sugar, current.items[1]] };
    return response(sugar);
  };
  explained = () =>
    response({
      status: "Explained",
      explanation: "Flour: 400 g on hand and 600 g committed to released plans. Suggested reorder: 600 g.",
      notice: null,
      items: current.items.filter((i) => i.low),
    });
  fetchMock = vi.fn().mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : url === "/api/inventory/items"
        ? response(current)
        : url === "/api/inventory/receipts" && posted
          ? posted()
          : url === `/api/inventory/items/${SUGAR}/reorder-level`
            ? levelSaved()
            : url === "/api/inventory/low-stock/explanation"
              ? explained()
              : response({}, 404),
  );
  vi.stubGlobal("fetch", fetchMock);
});

const receiptCalls = () => fetchMock.mock.calls.filter(([url]) => url === "/api/inventory/receipts");
const stockRow = async (name: RegExp) =>
  within(await screen.findByRole("table", { name: "Stock levels" })).getByRole("row", { name });
const lowStock = async () => within(await screen.findByRole("table", { name: "Low-stock alerts" }));

async function fillReceipt(quantity: string, reference = "inv-1001") {
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("Ingredient"), "Flour (g)");
  await user.type(screen.getByLabelText(/Quantity received/), quantity);
  await user.type(screen.getByLabelText("Reference number"), reference);
  return user;
}

it("shows on hand, committed and available stock in a readable form", async () => {
  render(<App />);
  const flour = await stockRow(/Flour/);
  expect(within(flour).getByText("400 g")).toBeInTheDocument();
  expect(within(flour).getByText("600 g")).toBeInTheDocument();
  expect(within(flour).getByText("−200 g")).toBeInTheDocument();
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
  expect(await screen.findByText("Nothing is below its reorder level.")).toBeInTheDocument();
  expect(within(await stockRow(/Flour/)).getByText("2,900 g")).toBeInTheDocument();
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

// --- BAKE-37: low-stock alerts and reorder levels ---------------------------

it("lists low ingredients with the server's suggestion and explains them only when asked", async () => {
  const user = userEvent.setup();
  render(<App />);
  const flour = within((await lowStock()).getByRole("row", { name: /Flour/ }));
  expect(flour.getByText("−200.000 g")).toBeInTheDocument();
  expect(flour.getByText("200.000 g")).toBeInTheDocument();
  expect(flour.getByText("600.000 g")).toBeInTheDocument();
  expect((await lowStock()).queryByRole("row", { name: /Caster sugar/ })).not.toBeInTheDocument();
  expect(screen.getByText(/Nothing is ordered for you/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/inventory/low-stock/explanation")).toBe(false);

  await user.click(screen.getByRole("button", { name: "Explain these alerts" }));

  expect(await screen.findByText(/Suggested reorder: 600 g\./)).toHaveTextContent(
    "Assistant’s explanation: Flour: 400 g on hand",
  );
  const [, init] = fetchMock.mock.calls.find(([url]) => url === "/api/inventory/low-stock/explanation")!;
  expect(init.method).toBe("POST");
  expect(init.headers["X-CSRF-Token"]).toBe("t");
});

it("drops an earlier explanation once a receipt is recorded", async () => {
  render(<App />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Explain these alerts" }));
  expect(await screen.findByText(/Suggested reorder: 600 g\./)).toBeInTheDocument();

  await fillReceipt("2500");
  await user.click(screen.getByRole("button", { name: "Record receipt" }));

  expect(await screen.findByText("Nothing is below its reorder level.")).toBeInTheDocument();
  expect(screen.queryByText(/Suggested reorder: 600 g\./)).not.toBeInTheDocument();
});

it("keeps the alerts and shows the notice when no explanation is available", async () => {
  const user = userEvent.setup();
  explained = () =>
    response({
      status: "Unavailable",
      explanation: null,
      notice: "Explanation temporarily unavailable. The low-stock list is unchanged.",
      items: current.items.filter((i) => i.low),
    });
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Explain these alerts" }));

  expect(
    await screen.findByText("Explanation temporarily unavailable. The low-stock list is unchanged."),
  ).toBeInTheDocument();
  expect((await lowStock()).getByRole("row", { name: /Flour/ })).toBeInTheDocument();
});

it("saves a reorder level for the version the owner saw", async () => {
  const user = userEvent.setup();
  render(<App />);
  const input = await screen.findByLabelText("Reorder level for Caster sugar (g)");
  expect(input).toHaveValue("300.000");
  expect(screen.getByRole("button", { name: "Save the reorder level for Caster sugar" })).toBeDisabled();
  await user.clear(input);
  await user.type(input, "2500");
  await user.click(screen.getByRole("button", { name: "Save the reorder level for Caster sugar" }));

  expect(await screen.findByText("Caster sugar reorder level is now 2500.000 g.")).toBeInTheDocument();
  const [, init] = fetchMock.mock.calls.find(([url]) => url === `/api/inventory/items/${SUGAR}/reorder-level`)!;
  expect(init.method).toBe("PUT");
  expect(JSON.parse(init.body)).toEqual({ reorderLevel: "2500", version: 1 });
  expect(init.headers["X-CSRF-Token"]).toBe("t");
  expect((await lowStock()).getByRole("row", { name: /Caster sugar/ })).toHaveTextContent("3000.000 g");
});

it("shows the server's reason when the ingredient changed since it was loaded", async () => {
  const user = userEvent.setup();
  levelSaved = () =>
    response({ detail: "This ingredient changed since you loaded it. Reload to see its current stock." }, 409);
  render(<App />);
  const input = await screen.findByLabelText("Reorder level for Caster sugar (g)");
  await user.clear(input);
  await user.type(input, "250");
  await user.click(screen.getByRole("button", { name: "Save the reorder level for Caster sugar" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("This ingredient changed since you loaded it.");
});

it.each(["-1", "1.0005", "abc", ""])("does not save a reorder level of %j", async (level) => {
  const user = userEvent.setup();
  render(<App />);
  const input = await screen.findByLabelText("Reorder level for Caster sugar (g)");
  await user.clear(input);
  if (level) await user.type(input, level);

  expect(screen.getByRole("button", { name: "Save the reorder level for Caster sugar" })).toBeDisabled();
  expect(input).toHaveAttribute("aria-invalid", "true");
});

it("refuses a low item without a suggested reorder", async () => {
  current = { items: [{ ...flourItem("400.000", "-200.000"), suggestedReorder: null }] };
  render(<App />);
  expect(await screen.findByText(/returned incomplete data/)).toBeInTheDocument();
});

it("refuses stock levels sent as numbers instead of decimal strings", async () => {
  current = { items: [{ ...stock("400.000", "-200.000").items[1], onHand: 400 as unknown as string }] };
  render(<App />);
  expect(await screen.findByText(/returned incomplete data/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Record receipt" })).not.toBeInTheDocument();
});

it("marks low rows in the stock table and offers Save only on the row being edited", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(within(await stockRow(/Flour/)).getByText("Low")).toHaveAttribute("data-status", "Low");
  expect(within(await stockRow(/Caster sugar/)).queryByText("Low")).not.toBeInTheDocument();
  const input = await screen.findByLabelText("Reorder level for Caster sugar (g)");
  expect(input.closest("form")).toHaveAttribute("data-edited", "false");
  await user.type(input, "5");
  expect(input.closest("form")).toHaveAttribute("data-edited", "true");
});

it("shows each low ingredient as a label that screen readers skip in favour of the table", async () => {
  render(<App />);
  await lowStock();
  const labels = document.querySelector(".labels")!;
  expect(labels).toHaveAttribute("aria-hidden", "true");
  expect(labels).toHaveTextContent("Flour");
  expect(labels).toHaveTextContent("600g");
  expect(labels).not.toHaveTextContent("Caster sugar");
  // Available is below zero, so the bar stays empty.
  expect(labels.querySelector<HTMLElement>(".gauge i")).toHaveStyle({ width: "0%" });
});

it.each([
  ["3500.000", "3,500"],
  ["12.500", "12.5"],
  ["12.345", "12.345"],
  ["-200.000", "−200"],
  ["-1234.500", "−1,234.5"],
  ["0.000", "0"],
  ["-0.000", "0"],
  ["0.005", "0.005"],
  ["100.000", "100"],
  ["1000", "1,000"],
  ["123456789.120", "123,456,789.12"],
])("reads %s as %s without rounding", (value, expected) => {
  expect(readableQuantity(value)).toBe(expected);
});

it("keeps the exact figures in the alerts table, the receipt message and the level box", async () => {
  render(<App />);
  const flour = within((await lowStock()).getByRole("row", { name: /Flour/ }));
  expect(flour.getByText("−200.000 g")).toBeInTheDocument();
  expect(screen.getByLabelText("Reorder level for Flour (g)")).toHaveValue("200.000");
  const filled = await fillReceipt("2500");
  await filled.click(screen.getByRole("button", { name: "Record receipt" }));
  expect(await screen.findByText(/Flour on hand is now 2900.000 g\./)).toBeInTheDocument();
});
