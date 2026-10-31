import { ApiError, DATA_CHANGED, type OrderSummary } from "./client";

export type ReviewedSummary = OrderSummary & { allergen_statement?: string | null };
export type RecipeChoice = { productId: string; recipeId: string; recipeVersion: number };
export type CatalogueItem = RecipeChoice & { name: string; ingredients: string[] };
export type Resolution = { summary: ReviewedSummary | null; recipes: RecipeChoice[]; note: string };
export type Referral = {
  id: string; version: number; kind: "Intake" | "Refund" | "InProductionChange";
  status: "Pending" | "NeedsClarification" | "AwaitingCustomer" | "Completed" | "Closed";
  orderId: string | null; message: string; summary: ReviewedSummary | null;
  conversation: { role: "Owner" | "Customer"; text: string }[];
  resolution: Resolution | null; createdAt: string;
};
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === "string");
function summary(v: unknown): boolean {
  return v === null || (object(v) && Array.isArray(v.items) && v.items.every(i => object(i) && typeof i.product === "string" && (i.quantity === null || Number.isInteger(i.quantity)) && [i.flavour, i.theme, i.customisation].every(x => x == null || typeof x === "string")) &&
    (v.required_at === null || (typeof v.required_at === "string" && Number.isFinite(Date.parse(v.required_at)))) && ["at", "before"].includes(String(v.collection_kind)) && (v.budget === null || typeof v.budget === "string") && (v.allergen_concerns === null || strings(v.allergen_concerns)));
}
function referral(v: unknown): v is Referral {
  return object(v) && typeof v.id === "string" && Number.isInteger(v.version) &&
    ["Intake", "Refund", "InProductionChange"].includes(String(v.kind)) &&
    ["Pending", "NeedsClarification", "AwaitingCustomer", "Completed", "Closed"].includes(String(v.status)) &&
    typeof v.message === "string" && summary(v.summary) && Array.isArray(v.conversation) &&
    v.conversation.every(m => object(m) && ["Owner", "Customer"].includes(String(m.role)) && typeof m.text === "string") &&
    (v.resolution === null || (object(v.resolution) && typeof v.resolution.note === "string" && summary(v.resolution.summary) && Array.isArray(v.resolution.recipes)));
}
async function request(path: string, csrf?: string, body?: unknown): Promise<unknown> {
  const response = await fetch(`/api${path}`, { credentials: "same-origin", cache: "no-store", ...(body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" }, body: JSON.stringify(body),
  }) });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new ApiError(response.status, typeof data?.detail === "string" ? data.detail : "The review could not be completed. Please refresh and retry.");
  }
  if (body !== undefined) window.dispatchEvent(new Event(DATA_CHANGED));
  return response.json();
}
const invalid = () => { throw new Error("The server returned an invalid review. Please refresh."); };
export const referrals = {
  async list(): Promise<Referral[]> {
    const v = await request("/exception-requests");
    if (!object(v) || !Array.isArray(v.requests) || !v.requests.every(referral)) return invalid();
    return v.requests;
  },
  async catalogue(): Promise<CatalogueItem[]> {
    const v = await request("/owner/exception-catalogue");
    if (!object(v) || !Array.isArray(v.products) || !v.products.every(p => object(p) &&
      [p.productId, p.recipeId, p.name].every(x => typeof x === "string") && Number.isInteger(p.recipeVersion) && strings(p.ingredients))) return invalid();
    return v.products as CatalogueItem[];
  },
  async action(row: Referral, action: "question" | "resolve" | "reply" | "confirm", csrf: string, fields = {}): Promise<Referral> {
    const prefix = ["question", "resolve"].includes(action) ? "/owner" : "";
    const v = await request(`${prefix}/exception-requests/${encodeURIComponent(row.id)}/${action}`, csrf, { version: row.version, ...fields });
    if (!referral(v)) return invalid();
    return v;
  },
  async create(kind: "Refund" | "InProductionChange", orderId: string, message: string, csrf: string): Promise<Referral> {
    const v = await request("/exception-requests", csrf, { kind, orderId, message });
    if (!referral(v)) return invalid();
    return v;
  },
};
