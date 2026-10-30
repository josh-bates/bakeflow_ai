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
/** Stock quantities are decimal strings with three places, never floats (BAKE-32). */
export type StockItem = {
  id: string;
  ingredient: string;
  unit: string;
  onHand: string;
  committed: string;
  available: string;
};
export type ReceiptSource = "Invoice" | "DeliveryNote";
export type ReceiptInput = {
  itemId: string;
  quantity: string;
  sourceType: ReceiptSource;
  sourceReference: string;
};
export type StockReceipt = ReceiptInput & {
  id: string;
  ingredient: string;
  unit: string;
  receivedAt: string;
  onHand: string;
  /** True when this receipt was already recorded and stock was not raised again. */
  replayed: boolean;
};
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

/** UC-04 plans (BAKE-21). Task times come in the bakery's time zone. */
export type ScheduleTask = {
  id: string;
  orderId: string;
  kind: string;
  startAt: string;
  endAt: string;
  status: string;
};
export type ScheduleVersion = { version: number; startDate: string; endDate: string; tasks: ScheduleTask[] };
export type ScheduleState = "Draft" | "AwaitingApproval" | "NeedsRevision" | "Approved";
export type Schedule = {
  id: string;
  version: number;
  state: ScheduleState;
  releasedVersion: number | null;
  notice: string | null;
  latest: ScheduleVersion;
  released: ScheduleVersion | null;
};
export type Schedules = { timeZone: string; schedules: Schedule[] };
export type PlanResult = {
  status: "Proposed" | "NeedsRevision" | "NoCandidates";
  schedule: { recordId: string; version: number } | null;
  rationale: string | null;
  confidence: number | null;
  problems: string[];
  excluded: { orderId: string; reason: string }[];
};
export type ReleaseResult = { status: "Released" | "ReleaseBlocked" | "Withheld"; problems: string[] };

/** UC-05 quotes (BAKE-23). Money is a decimal string, never a float. */
export type OwnerQuote = {
  id: string;
  version: number;
  orderId: string;
  materialCost: string;
  labourCost: string;
  packagingCost: string;
  additionalCost: string;
  targetMargin: string;
  total: string;
  customerDecision: "Pending" | "Accepted" | "Rejected";
  requiresManualReview: boolean;
  explanation: string | null;
};
export type QuoteApproval = "Pending" | "Approved" | "Withheld";
export type QuoteQueueItem = {
  orderId: string;
  summary: string;
  requiredAt: string;
  orderStatus: string;
  /** The order has a stock check for its current version, so it can be priced. */
  ready: boolean;
  approval: QuoteApproval | null;
  quote: OwnerQuote | null;
};
/** What the customer sees: the price to accept, not the bakery's costs. */
export type CustomerQuote = {
  id: string;
  version: number;
  orderId: string;
  total: string;
  customerDecision: "Pending" | "Accepted" | "Rejected";
};
export type ExtraCost = { description: string; amount: string };

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
const QUANTITY = /^-?\d+\.\d{3}$/;
function quantity(value: unknown): boolean {
  return typeof value === "string" && QUANTITY.test(value);
}
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
const DECISIONS = ["Pending", "Accepted", "Rejected"];
function money(value: unknown): boolean {
  return typeof value === "string" && MONEY.test(value);
}
function ownerQuote(value: unknown): boolean {
  return (
    record(value) &&
    typeof value.id === "string" &&
    Number.isInteger(value.version) &&
    typeof value.orderId === "string" &&
    ["materialCost", "labourCost", "packagingCost", "additionalCost", "total"].every((k) => money(value[k])) &&
    typeof value.targetMargin === "string" &&
    DECISIONS.includes(String(value.customerDecision)) &&
    typeof value.requiresManualReview === "boolean" &&
    (value.explanation === null || typeof value.explanation === "string")
  );
}
function customerQuote(value: unknown): boolean {
  return (
    record(value) &&
    typeof value.id === "string" &&
    Number.isInteger(value.version) &&
    typeof value.orderId === "string" &&
    money(value.total) &&
    DECISIONS.includes(String(value.customerDecision))
  );
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((v) => typeof v === "string");
}
function scheduleVersion(value: unknown): boolean {
  return (
    record(value) &&
    Number.isInteger(value.version) &&
    typeof value.startDate === "string" &&
    typeof value.endDate === "string" &&
    Array.isArray(value.tasks) &&
    value.tasks.every(
      (t) => record(t) && ["id", "orderId", "kind", "startAt", "endAt", "status"].every((k) => typeof t[k] === "string"),
    )
  );
}
function schedule(value: unknown): boolean {
  return (
    record(value) &&
    typeof value.id === "string" &&
    Number.isInteger(value.version) &&
    ["Draft", "AwaitingApproval", "NeedsRevision", "Approved"].includes(String(value.state)) &&
    (value.releasedVersion === null || Number.isInteger(value.releasedVersion)) &&
    (value.notice === null || typeof value.notice === "string") &&
    scheduleVersion(value.latest) &&
    (value.released === null || scheduleVersion(value.released))
  );
}
const JSON_POST = (csrfToken: string, body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
  body: JSON.stringify(body),
});
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
  async schedules(): Promise<Schedules> {
    const value = await request("/schedules");
    if (!record(value) || typeof value.timeZone !== "string" || !Array.isArray(value.schedules) ||
        !value.schedules.every(schedule)) return invalid();
    return value as Schedules;
  },
  /** Asks for a new plan, or a revised one when ``scheduleId`` is given. */
  async planSchedule(startDate: string, endDate: string, csrfToken: string, scheduleId?: string): Promise<PlanResult> {
    const path = scheduleId ? `/schedules/${encodeURIComponent(scheduleId)}/revisions` : "/schedules";
    const value = await request(path, JSON_POST(csrfToken, { startDate, endDate }));
    if (
      !record(value) ||
      !["Proposed", "NeedsRevision", "NoCandidates"].includes(String(value.status)) ||
      !(value.schedule === null ||
        (record(value.schedule) && typeof value.schedule.recordId === "string" && Number.isInteger(value.schedule.version))) ||
      !(value.rationale === null || typeof value.rationale === "string") ||
      !(value.confidence === null || typeof value.confidence === "number") ||
      !strings(value.problems) ||
      !Array.isArray(value.excluded) ||
      !value.excluded.every((e) => record(e) && typeof e.orderId === "string" && typeof e.reason === "string")
    )
      return invalid();
    return value as PlanResult;
  },
  /** Approves (and so tries to release) exactly ``version``, or withholds approval for now. */
  async decideSchedule(scheduleId: string, version: number, decision: "Approved" | "Withheld",
                       csrfToken: string): Promise<ReleaseResult> {
    const value = await request(`/schedules/${encodeURIComponent(scheduleId)}/decision`,
      JSON_POST(csrfToken, { version, decision }));
    if (!record(value) || !["Released", "ReleaseBlocked", "Withheld"].includes(String(value.status)) ||
        !strings(value.problems)) return invalid();
    return value as ReleaseResult;
  },
  /** The owner's open orders with their latest quote version (BAKE-23). */
  async quoteQueue(): Promise<QuoteQueueItem[]> {
    const value = await request("/finance/quotes");
    if (
      !record(value) ||
      !Array.isArray(value.orders) ||
      !value.orders.every(
        (o) =>
          record(o) &&
          ["orderId", "summary", "requiredAt", "orderStatus"].every((k) => typeof o[k] === "string") &&
          typeof o.ready === "boolean" &&
          (o.approval === null || ["Pending", "Approved", "Withheld"].includes(String(o.approval))) &&
          (o.quote === null || ownerQuote(o.quote)),
      )
    )
      return invalid();
    return value.orders as QuoteQueueItem[];
  },
  async draftQuote(orderId: string, additionalCosts: ExtraCost[], csrfToken: string): Promise<OwnerQuote> {
    const value = await request(`/finance/orders/${encodeURIComponent(orderId)}/quotes`,
      JSON_POST(csrfToken, { additionalCosts }));
    if (!ownerQuote(value)) return invalid();
    return value as OwnerQuote;
  },
  async reviseQuote(quote: OwnerQuote, additionalCosts: ExtraCost[], csrfToken: string): Promise<OwnerQuote> {
    const value = await request(
      `/finance/quotes/${encodeURIComponent(quote.id)}/versions/${quote.version}/revise`,
      JSON_POST(csrfToken, { additionalCosts }));
    if (!ownerQuote(value)) return invalid();
    return value as OwnerQuote;
  },
  /** Approves or withholds exactly this version. A withheld version must be revised. */
  async decideQuote(quote: OwnerQuote, decision: "approve" | "withhold", csrfToken: string): Promise<OwnerQuote> {
    const value = await request(
      `/finance/quotes/${encodeURIComponent(quote.id)}/versions/${quote.version}/${decision}`,
      JSON_POST(csrfToken, {}));
    if (!ownerQuote(value)) return invalid();
    return value as OwnerQuote;
  },
  /** The approved, current quote for one of the customer's orders, or null. */
  async customerQuote(orderId: string): Promise<CustomerQuote | null> {
    const value = await request(`/orders/${encodeURIComponent(orderId)}/quote`);
    if (!record(value) || !(value.quote === null || customerQuote(value.quote))) return invalid();
    return value.quote as CustomerQuote | null;
  },
  async acceptQuote(quote: CustomerQuote, csrfToken: string): Promise<{ orderId: string; replayed: boolean }> {
    const value = await request(
      `/quotes/${encodeURIComponent(quote.id)}/versions/${quote.version}/accept`, JSON_POST(csrfToken, {}));
    if (!record(value) || typeof value.orderId !== "string" || typeof value.replayed !== "boolean") return invalid();
    return value as { orderId: string; replayed: boolean };
  },
  async rejectQuote(quote: CustomerQuote, csrfToken: string): Promise<CustomerQuote> {
    const value = await request(
      `/quotes/${encodeURIComponent(quote.id)}/versions/${quote.version}/reject`, JSON_POST(csrfToken, {}));
    if (!customerQuote(value)) return invalid();
    return value as CustomerQuote;
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
  async stock(): Promise<StockItem[]> {
    const value = await request("/inventory/items");
    if (
      !record(value) ||
      !Array.isArray(value.items) ||
      !value.items.every(
        (i) =>
          record(i) &&
          [i.id, i.ingredient, i.unit].every((v) => typeof v === "string") &&
          [i.onHand, i.committed, i.available].every(quantity),
      )
    )
      return invalid();
    return value.items as StockItem[];
  },
  /** Records a delivered ingredient; repeating the same receipt never raises stock twice. */
  async receiveStock(receipt: ReceiptInput, csrfToken: string): Promise<StockReceipt> {
    const value = await request("/inventory/receipts", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(receipt),
    });
    if (
      !record(value) ||
      [value.id, value.itemId, value.ingredient, value.unit, value.sourceReference].some(
        (v) => typeof v !== "string",
      ) ||
      !["Invoice", "DeliveryNote"].includes(String(value.sourceType)) ||
      !quantity(value.quantity) ||
      String(value.quantity).startsWith("-") ||
      !quantity(value.onHand) ||
      typeof value.receivedAt !== "string" ||
      !Number.isFinite(Date.parse(value.receivedAt)) ||
      typeof value.replayed !== "boolean"
    )
      return invalid();
    return value as StockReceipt;
  },
};
