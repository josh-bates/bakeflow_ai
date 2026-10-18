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
export type Submission = { requestId: string; message: string };

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
    throw new ApiError(
      response.status,
      messages[response.status] ??
        "The bakery service could not complete this request. Please try again.",
    );
  }
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
function invalid(): never {
  throw new ApiError(
    502,
    "The bakery service returned incomplete data. Please try again.",
  );
}

export const api = {
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
      typeof value.requestId !== "string" ||
      !value.requestId ||
      typeof value.message !== "string"
    )
      return invalid();
    return value as Submission;
  },
};
