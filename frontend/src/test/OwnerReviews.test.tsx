import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { OwnerReviews } from "../components/OwnerReviews";
import { type Referral, type Resolution } from "../api/referrals";

const response = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const session = { user: { displayName: "Customer", role: "Customer" as const }, mode: "mock" as const, csrfToken: "csrf" };
const details = { items: [{ product: "Vanilla cupcake", quantity: 24, flavour: "vanilla", theme: "birthday", customisation: "owner reviewed" }],
  required_at: "2027-11-05T12:00:00+11:00", collection_kind: "before" as const, allergen_concerns: ["milk concern"], allergen_statement: "milk concern", budget: null };
const resolution: Resolution = { summary: details, note: "Please check this agreed product and recipe.", recipes: [{ productId: "p", recipeId: "r", recipeVersion: 1 }] };
let row: Referral;
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  row = { id: "review-1", version: 2, kind: "Intake", status: "AwaitingCustomer", orderId: null,
    message: "Please replace the milk", summary: details, resolution, conversation: [], createdAt: "2026-10-29T00:00:00Z" };
  fetchMock = vi.fn(async (url: string) => url === "/api/owner/exception-catalogue" ? response({ products: [{ productId: "p", recipeId: "r", recipeVersion: 1, name: "Vanilla cupcake", ingredients: ["Milk"] }] }) : response({ requests: [row] }));
  vi.stubGlobal("fetch", fetchMock);
});

it("shows the resolved details and only saves after explicit confirmation", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/confirm")) {
      expect(JSON.parse(options!.body as string)).toEqual({ version: 2 });
      expect(options!.headers).toMatchObject({ "X-CSRF-Token": "csrf" });
      row = { ...row, status: "Completed", orderId: "order-1" };
      return response(row);
    }
    return response({ requests: [row] });
  });
  render(<OwnerReviews session={session} />);
  expect(await screen.findByText("milk concern")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/confirm"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Confirm reviewed details and save draft" }));
  expect(await screen.findByText(/Draft saved after your confirmation/)).toBeInTheDocument();
});

it("keeps the proposal visible after a failed save and never shows success", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string) => url.endsWith("/confirm") ? response({ detail: "Save unavailable" }, 503) : response({ requests: [row] }));
  render(<OwnerReviews session={session} />);
  await user.click(await screen.findByRole("button", { name: "Confirm reviewed details and save draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Save unavailable");
  expect(screen.queryByText(/Draft saved after/)).not.toBeInTheDocument();
  expect(screen.getByText(resolution.note)).toBeInTheDocument();
});

it("sends corrections back to the owner instead of silently saving them", async () => {
  const user = userEvent.setup();
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/reply")) {
      expect(JSON.parse(options!.body as string)).toEqual({ version: 2, text: "Also eggs" });
      row = { ...row, version: 3, status: "Pending", resolution: null };
      return response(row);
    }
    return response({ requests: [row] });
  });
  render(<OwnerReviews session={session} />);
  await user.type(await screen.findByLabelText("Answer or correction"), "Also eggs");
  await user.click(screen.getByRole("button", { name: "Send to owner for review" }));
  await screen.findByText("Waiting for the owner");
  expect(screen.queryByRole("button", { name: "Confirm reviewed details and save draft" })).not.toBeInTheDocument();
});

it("requires the owner to select a catalogue recipe and preserves the review version", async () => {
  row = { ...row, status: "Pending", resolution: null, version: 1 };
  const user = userEvent.setup();
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/resolve")) {
      const body = JSON.parse(options!.body as string);
      expect(body.version).toBe(1);
      expect(body.resolution.recipes).toEqual(resolution.recipes);
      row = { ...row, version: 2, status: "AwaitingCustomer", resolution: body.resolution };
      return response(row);
    }
    return original(url);
  });
  render(<OwnerReviews session={{ ...session, user: { displayName: "Owner", role: "Owner" } }} />);
  const select = await screen.findByLabelText("Product and recipe");
  expect(select).toHaveValue("");
  await user.selectOptions(select, "p");
  expect(screen.getByText("Ingredients: Milk")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Explain the selected recipe and agreed handling"), "Discussed with customer");
  await user.click(screen.getByRole("button", { name: "Send resolution to customer" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/resolve"))).toBe(true));
  await screen.findByText("Ready for your review");
});

it("labels an acknowledged refund as review only, never as money refunded", async () => {
  row = { ...row, kind: "Refund", status: "Completed", resolution: { summary: null, recipes: [], note: "Owner will discuss the refund." } };
  render(<OwnerReviews session={session} />);
  expect(await screen.findByText(/No refund, order change or production change has been applied/)).toBeInTheDocument();
});
