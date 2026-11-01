import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { OrderChanges } from "../customer/OrderChanges";
import { OrderChangeReviews } from "../owner/OrderChangeReviews";
import { changes } from "../api/orderChanges";
import { ApiError, type Session } from "../api/client";
vi.mock("../api/orderChanges", () => ({ changes: { preview: vi.fn(), confirm: vi.fn(), list: vi.fn(), action: vi.fn(), decide: vi.fn() } }));
const session: Session = { user: { displayName: "Customer", role: "Customer" }, mode: "mock", csrfToken: "csrf" };
const orders = [{ id: "order-1", summary: "24 cupcakes", status: "Draft", requiredAt: "2027-10-02T02:00:00Z" }];
const proposal = { current: { status: "Draft", quantity: 24, requiredAt: orders[0].requiredAt, collectionKind: "at" }, proposed: { scope: "Quantity", quantity: 36 }, outcome: "Save new terms after confirmation.", route: "Apply", confirmationToken: "signed-preview" };
const request = { id: "req-1", orderId: "order-1", version: 1, kind: "Change" as const, status: "Pending", message: "change quantity to 36", proposed: { scope: "Quantity", quantity: 36, route: "Requote" }, ownerNote: null, quoteTotal: null, createdAt: "2026-10-31T00:00:00Z" };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(changes.list).mockResolvedValue([]); vi.mocked(changes.preview).mockResolvedValue(proposal); });
async function openPreview() {
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Order", {exact:true}), "order-1");
  await user.type(screen.getByLabelText("Describe your request"), "change quantity to 36");
  await user.click(screen.getByRole("button", {name:"Preview request"}));
  await screen.findByRole("heading", {name:"Review the proposed outcome"});
  return user;
}
it("preview and withdrawal never save an unconfirmed change", async () => {
  render(<OrderChanges orders={orders} session={session} onExpired={() => {}} onChanged={() => {}} />);
  const user = await openPreview();
  expect(changes.confirm).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", {name:"Withdraw without saving"}));
  expect(screen.queryByRole("button", {name:"Confirm this request"})).not.toBeInTheDocument();
  expect(screen.getByText("Preview withdrawn. Your order is unchanged.")).toBeInTheDocument();
  expect(changes.confirm).not.toHaveBeenCalled();
});
it("reports failed saves, keeps the same confirmation for retry and reports success only afterwards", async () => {
  const changed = vi.fn();
  vi.mocked(changes.confirm).mockRejectedValueOnce(new ApiError(503, "Saving was not confirmed.")).mockResolvedValue({...request,status:"Applied"});
  render(<OrderChanges orders={orders} session={session} onExpired={() => {}} onChanged={changed} />);
  const user = await openPreview();
  await user.click(screen.getByRole("button", {name:"Confirm this request"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Saving was not confirmed");
  expect(changed).not.toHaveBeenCalled();
  expect(screen.queryByText("Your confirmed request was saved.")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name:"Confirm this request"}));
  expect(await screen.findByText("Your confirmed request was saved.")).toBeInTheDocument();
  expect(vi.mocked(changes.confirm).mock.calls).toEqual([["signed-preview","csrf"],["signed-preview","csrf"]]);
});
it("stale requests require a fresh preview", async () => {
  vi.mocked(changes.confirm).mockRejectedValue(new ApiError(409, "The order changed."));
  render(<OrderChanges orders={orders} session={session} onExpired={() => {}} onChanged={() => {}} />);
  const user = await openPreview();
  await user.click(screen.getByRole("button", {name:"Confirm this request"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("The order changed");
  expect(screen.queryByRole("button", {name:"Confirm this request"})).not.toBeInTheDocument();
});
it("shows the revised terms and price before customer acceptance", async () => {
  vi.mocked(changes.list).mockResolvedValue([{...request, status:"Quoted", quoteTotal:"141.34", version:3}]);
  vi.mocked(changes.action).mockResolvedValue({...request,status:"Applied",version:4});
  render(<OrderChanges orders={orders} session={session} onExpired={() => {}} onChanged={() => {}} />);
  expect(await screen.findByText("AUD 141.34")).toBeInTheDocument();
  expect(screen.getByText("36")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", {name:"Accept revised quotation and apply change"}));
  expect(changes.action).toHaveBeenCalledWith("req-1",3,"accept","csrf");
});
it("owner sees the calculated quotation before approving its exact request version", async () => {
  vi.mocked(changes.list).mockResolvedValue([{...request,status:"AwaitingApproval",quoteTotal:"141.34",version:2}]);
  vi.mocked(changes.decide).mockResolvedValue({...request,status:"Quoted",version:3});
  render(<OrderChangeReviews session={{...session,user:{displayName:"Owner",role:"Owner"}}} />);
  expect(await screen.findByText("AUD 141.34")).toBeInTheDocument();
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Reply to customer"), "Approved after checking");
  await user.click(screen.getByRole("button",{name:"Approve this revised quotation"}));
  await waitFor(() => expect(changes.decide).toHaveBeenCalledWith("req-1",2,"ApproveQuote","Approved after checking","csrf"));
});
it("returns to sign-in when the owner review list session expires", async () => {
  const expired = vi.fn();
  vi.mocked(changes.list).mockRejectedValue(new ApiError(401, "Sign in again."));
  render(<OrderChangeReviews session={session} onExpired={expired} />);
  await waitFor(() => expect(expired).toHaveBeenCalledOnce());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("returns to sign-in when an owner decision session expires", async () => {
  const expired = vi.fn();
  vi.mocked(changes.list).mockResolvedValue([request]);
  vi.mocked(changes.decide).mockRejectedValue(new ApiError(401, "Sign in again."));
  render(<OrderChangeReviews session={session} onExpired={expired} />);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Reply to customer"), "Please clarify");
  await user.click(screen.getByRole("button", { name: "Send review note" }));
  await waitFor(() => expect(expired).toHaveBeenCalledOnce());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("points the owner to Finance to record a refund", async () => {
  vi.mocked(changes.list).mockResolvedValue([{ ...request, kind: "Refund", orderId: "abcdef12-0000-0000-0000-000000000000",
    message: "Please refund the deposit", proposed: {} }]);
  render(<OrderChangeReviews session={{ ...session, user: { displayName: "Owner", role: "Owner" } }} />);
  const link = await screen.findByRole("link", { name: "record the refund in Finance" });
  expect(link).toHaveAttribute("href", "#/owner/finance");
  expect(screen.getByText(/REFUND-abcdef12/)).toBeInTheDocument();
});
