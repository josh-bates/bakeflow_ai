export type Session = {
  user: { displayName: string; role: "Customer" | "Owner" };
  mode: "mock" | "live";
  csrfToken: string;
};
export type Order = {
  id: string;
  summary: string;
  status: string;
  requiredAt: string | null;
};
export type Tracking = { id: string; status: string; version: number; requiredAt: string; explanation: string; explanationAvailable: boolean };
export type OrderSummary = {
  items: { product: string; quantity: number | null; flavour: string | null; theme: string | null; customisation: string | null }[];
  required_at: string | null;
  collection_kind: "at" | "before";
  allergen_concerns: string[] | null;
  budget: string | null;
};
export type Submission = { summary: OrderSummary; issues: string[]; confirmationToken: string | null };
export type DraftReceipt = { orderId: string; status: "Draft" };
/** Money is a decimal string from the server, never a float (BAKE-34). */
export type ProfitLine = {
  income: string;
  expenses: string;
  profit: string;
  margin: string | null;
  transactions: number;
};
export type Profitability = {
  overall: ProfitLine;
  general: ProfitLine;
  byOrder: (ProfitLine & { orderId: string })[];
  byProduct: (ProfitLine & { productId: string; name: string })[];
  periodStart: string | null;
  periodEnd: string | null;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...init.headers },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the bakery service. Please try again.");
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Your session has ended. Please sign in again.",
      403: "You do not have permission to perform this action.",
      404: "This service is not available yet. Please try again later.",
      422: "The request could not be accepted. Please check your details.",
      429: "Too many requests. Please wait before trying again.",
    };
    if ([409, 503].includes(response.status)) {
      const data = await response.json().catch(() => null);
      if (record(data) && typeof data.detail === "string")
        throw new ApiError(response.status, data.detail);
    }
    throw new ApiError(
      response.status,
      messages[response.status] ??
        "The bakery service could not complete this request. Please try again.",
    );
  }
  if (response.status === 204) return undefined;
  try {
    return await response.json();
  } catch {
    throw new ApiError(
      502,
      "The bakery service returned an unreadable response. Please try again.",
    );
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}
const MONEY = /^-?\d+\.\d{2}$/;
function profitLine(value: unknown): boolean {
  return (
    record(value) &&
    [value.income, value.expenses, value.profit].every(
      (v) => typeof v === "string" && MONEY.test(v),
    ) &&
    (value.margin === null ||
      (typeof value.margin === "string" && /^-?\d+\.\d{4}$/.test(value.margin))) &&
    Number.isInteger(value.transactions)
  );
}
function invalid(): never {
  throw new ApiError(
    502,
    "The bakery service returned incomplete data. Please try again.",
  );
}

export const api = {
  async login(email: string, password: string): Promise<void> {
    try {
      await request("/auth/login", { method: "POST", headers: {
        "Content-Type": "application/json", "X-Requested-With": "BakeFlow",
      }, body: JSON.stringify({ email, password }) });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401)
        throw new ApiError(401, "Email or password is incorrect.");
      throw error;
    }
  },
  async logout(csrfToken: string): Promise<void> {
    await request("/auth/logout", { method: "POST", headers: { "X-CSRF-Token": csrfToken } });
  },
  async session(): Promise<Session> {
    const value = await request("/session");
    if (
      !record(value) ||
      !record(value.user) ||
      typeof value.user.displayName !== "string" ||
      !["Customer", "Owner"].includes(String(value.user.role)) ||
      !["mock", "live"].includes(String(value.mode)) ||
      typeof value.csrfToken !== "string" ||
      !value.csrfToken
    )
      return invalid();
    return value as Session;
  },
  async tracking(orderId: string): Promise<Tracking> {
    const value = await request(`/orders/${encodeURIComponent(orderId)}/tracking`);
    if (!record(value) || typeof value.id !== "string" || typeof value.status !== "string" ||
        !Number.isInteger(value.version) || typeof value.requiredAt !== "string" ||
        typeof value.explanation !== "string" || typeof value.explanationAvailable !== "boolean") return invalid();
    return value as Tracking;
  },
  async orders(): Promise<Order[]> {
    const value = await request("/orders/mine");
    if (
      !record(value) ||
      !Array.isArray(value.orders) ||
      !value.orders.every(
        (item) =>
          record(item) &&
          typeof item.id === "string" &&
          typeof item.summary === "string" &&
          typeof item.status === "string" &&
          (item.requiredAt === null ||
            (typeof item.requiredAt === "string" &&
              Number.isFinite(Date.parse(item.requiredAt)))),
      )
    )
      return invalid();
    return value.orders;
  },
  async submit(
    message: string,
    csrfToken: string,
    idempotencyKey: string,
  ): Promise<Submission> {
    const value = await request("/order-requests", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ message }),
    });
    if (
      !record(value) ||
      !record(value.summary) || !Array.isArray(value.summary.items) ||
      !value.summary.items.every((i) => record(i) && typeof i.product === "string" &&
        (i.quantity === null || Number.isInteger(i.quantity)) &&
        [i.flavour, i.theme, i.customisation].every(v => v === null || typeof v === "string")) ||
      !(value.summary.required_at === null || (typeof value.summary.required_at === "string" && Number.isFinite(Date.parse(value.summary.required_at)))) ||
      !["at", "before"].includes(String(value.summary.collection_kind)) ||
      !(value.summary.allergen_concerns === null || (Array.isArray(value.summary.allergen_concerns) && value.summary.allergen_concerns.every(v => typeof v === "string"))) ||
      !(value.summary.budget === null || typeof value.summary.budget === "string") ||
      !Array.isArray(value.issues) || !value.issues.every(v => typeof v === "string") ||
      !(value.confirmationToken === null || typeof value.confirmationToken === "string")
    )
      return invalid();
    return value as Submission;
  },
  async profitability(): Promise<Profitability> {
    const value = await request("/finance/profitability");
    if (
      !record(value) ||
      !profitLine(value.overall) ||
      !profitLine(value.general) ||
      !Array.isArray(value.byOrder) ||
      !value.byOrder.every((o) => profitLine(o) && record(o) && typeof o.orderId === "string") ||
      !Array.isArray(value.byProduct) ||
      !value.byProduct.every(
        (p) => profitLine(p) && record(p) && typeof p.productId === "string" && typeof p.name === "string",
      )
    )
      return invalid();
    return value as Profitability;
  },
  async confirm(confirmationToken: string, csrfToken: string): Promise<DraftReceipt> {
    const value = await request("/order-requests/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ confirmationToken }),
    });
    if (!record(value) || typeof value.orderId !== "string" || !value.orderId || value.status !== "Draft") return invalid();
    return value as DraftReceipt;
  },
};
