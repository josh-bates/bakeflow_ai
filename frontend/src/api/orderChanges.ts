import { request, ApiError } from "./client";
export type ChangeKind = "Change" | "Cancel" | "Refund";
export type Terms = { scope?: string; collection_kind?: string; quantity?: number | null; required_at?: string | null; evidence?: string; route?: string };
export type ChangePreview = {
  current: { status: string; quantity: number | null; requiredAt: string; collectionKind: string };
  proposed: Terms; outcome: string; route: string; confirmationToken: string;
};
export type ChangeRequest = {
  id: string; orderId: string; version: number; kind: ChangeKind; status: string; message: string;
  proposed: Terms; ownerNote: string | null; quoteTotal: string | null; createdAt: string;
};
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object";
function invalid(): never { throw new ApiError(502, "The service returned incomplete order-change information. Please refresh."); }
function row(value: unknown): ChangeRequest {
  if (!object(value) || ![value.id, value.orderId, value.kind, value.status, value.message, value.createdAt].every(v => typeof v === "string") ||
      !Number.isInteger(value.version) || !object(value.proposed) ||
      !(value.ownerNote === null || typeof value.ownerNote === "string") ||
      !(value.quoteTotal === null || typeof value.quoteTotal === "string")) return invalid();
  return value as ChangeRequest;
}
const post = (csrf: string, body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body) });
export const changes = {
  async preview(orderId: string, kind: ChangeKind, message: string, csrf: string): Promise<ChangePreview> {
    const v = await request(`/orders/${encodeURIComponent(orderId)}/changes/preview`, post(csrf, { kind, message }));
    if (!object(v) || !object(v.current) || !object(v.proposed) ||
        ![v.outcome, v.route, v.confirmationToken, v.current.status, v.current.requiredAt, v.current.collectionKind].every(x => typeof x === "string") ||
        !(v.current.quantity === null || Number.isInteger(v.current.quantity))) return invalid();
    return v as ChangePreview;
  },
  async confirm(confirmationToken: string, csrf: string) { return row(await request("/order-changes/confirm", post(csrf, { confirmationToken }))); },
  async list(owner = false): Promise<ChangeRequest[]> {
    const v = await request(owner ? "/owner/order-changes" : "/order-changes");
    if (!object(v) || !Array.isArray(v.requests)) return invalid();
    return v.requests.map(row);
  },
  async action(id: string, version: number, action: "withdraw" | "accept", csrf: string) {
    return row(await request(`/order-changes/${encodeURIComponent(id)}/${action}`, post(csrf, { version })));
  },
  async decide(id: string, version: number, decision: string, note: string, csrf: string) {
    return row(await request(`/owner/order-changes/${encodeURIComponent(id)}/decision`, post(csrf, { version, decision, note })));
  },
};
