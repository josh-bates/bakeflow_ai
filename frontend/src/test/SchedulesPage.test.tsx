import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const ID = "5c1d2e3f-0000-0000-0000-000000000000";
const task = (kind: string, startAt: string, endAt: string, status = "Planned") => ({
  id: `${kind}-${startAt}`,
  orderId: "0a1b2c3d-0000-0000-0000-000000000000",
  kind,
  startAt,
  endAt,
  status,
});
const version = (n: number, tasks: ReturnType<typeof task>[]) => ({
  version: n,
  startDate: "2026-11-05",
  endDate: "2026-11-05",
  tasks,
});
const awaiting = {
  id: ID,
  version: 2,
  state: "AwaitingApproval",
  releasedVersion: 1,
  notice: null,
  latest: version(2, [
    task("bake", "2026-11-05T06:00:00+11:00", "2026-11-05T07:00:00+11:00"),
    task("finish", "2026-11-05T07:30:00+11:00", "2026-11-05T08:00:00+11:00"),
  ]),
  released: version(1, [
    task("bake", "2026-11-05T06:00:00+11:00", "2026-11-05T07:00:00+11:00", "InProgress"),
    task("finish", "2026-11-05T07:00:00+11:00", "2026-11-05T07:30:00+11:00"),
  ]),
};
const blocked = {
  status: "ReleaseBlocked",
  schedule: { recordType: "ProductionSchedule", recordId: ID, version: 2 },
  problems: ["Stock no longer covers the plan: short of 200 g flour."],
};
const finish = awaiting.released.tasks[1];
const deducted = {
  taskId: finish.id,
  status: "Completed",
  order: { recordType: "Order", recordId: finish.orderId, version: 1 },
  orderStatus: "Completed",
  deduction: {
    id: "d0000000-0000-0000-0000-000000000000",
    scheduleVersion: 1,
    deductedAt: "2026-11-04T20:30:00+00:00",
    lines: [{ itemId: "f0000000-0000-0000-0000-000000000000", ingredient: "Flour", unit: "g", quantity: "600.000", onHand: "400.000" }],
  },
  replayed: false,
};
let fetchMock: ReturnType<typeof vi.fn>;
let schedules: unknown[];
let progressReply: Response;
beforeEach(() => {
  location.hash = "#/owner/schedules";
  schedules = [awaiting];
  progressReply = response(deducted);
  fetchMock = vi.fn().mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "csrf-1" })
      : url === "/api/schedules"
        ? response({ timeZone: "Australia/Sydney", schedules })
        : url === `/api/schedules/${ID}/decision`
          ? response(blocked)
          : url.startsWith(`/api/schedules/${ID}/tasks/`)
            ? progressReply
            : response({}, 404),
  );
  vi.stubGlobal("fetch", fetchMock);
});

it("shows the version under review beside the released one, with server times as given", async () => {
  render(<App />);
  const plan = await screen.findByRole("region", { name: /Plan 5c1d2e3f · 2026-11-05/ });
  expect(within(plan).getByText("Awaiting your approval")).toBeInTheDocument();
  expect(within(plan).getByText(/version 1 stays in use until this one is released/)).toBeInTheDocument();
  const [latest, released] = within(plan).getAllByRole("table");
  expect(within(latest).getByRole("row", { name: /finish/ })).toHaveTextContent("07:30–08:00");
  expect(within(released).getByRole("row", { name: /bake/ })).toHaveTextContent("In progress");
  expect(screen.getByRole("link", { name: "Schedules" })).toHaveAttribute("aria-current", "page");
});

it("approves exactly the reviewed version and reports a blocked release with its reasons", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Approve and release version 2" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Version 2 could not be released. Your approval is recorded, but nothing was released");
  expect(alert).toHaveTextContent("short of 200 g flour");
  const [url, init] = fetchMock.mock.calls.find(([u]) => u === `/api/schedules/${ID}/decision`)!;
  expect(url).toBe(`/api/schedules/${ID}/decision`);
  expect(JSON.parse(init.body)).toEqual({ version: 2, decision: "Approved" });
  expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
});

it("refuses a plan list with a task time that is not text", async () => {
  schedules = [{ ...awaiting, latest: { ...awaiting.latest, tasks: [{ ...awaiting.latest.tasks[0], startAt: 6 }] } }];
  render(<App />);
  expect(await screen.findByText(/returned incomplete data/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("offers progress only on the released version's tasks", async () => {
  render(<App />);
  const plan = await screen.findByRole("region", { name: /Plan 5c1d2e3f · 2026-11-05/ });
  const [latest, released] = within(plan).getAllByRole("table");
  expect(within(latest).queryByRole("button")).not.toBeInTheDocument();
  // The bake has started, so it can only be marked done; the finish can also start.
  expect(within(released).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
    "Mark the bake task for order 0a1b2c3d done",
    "Start the finish task for order 0a1b2c3d",
    "Mark the finish task for order 0a1b2c3d done",
  ]);
  expect(within(plan).getByRole("button", { name: "Not yet" })).toBeInTheDocument();
});

it("marks the order's last task done and shows the stock deducted once", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Mark the finish task for order 0a1b2c3d done" }));

  const status = await screen.findByText(/^Order 0a1b2c3d is complete\. Its ingredients were deducted once:/);
  expect(status).toHaveAttribute("role", "status");
  expect(status).toHaveTextContent("Flour: 600.000 g used, 400.000 g on hand now");
  const [url, init] = fetchMock.mock.calls.find(([u]) => String(u).includes("/tasks/"))!;
  expect(url).toBe(`/api/schedules/${ID}/tasks/${encodeURIComponent(finish.id)}/progress`);
  expect(JSON.parse(init.body)).toEqual({ status: "Completed" });
  expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
});

it("shows why the server refused the progress", async () => {
  const reason = "The finish task for order 0a1b2c3d (Thu 07:00–07:30) is paused: version 2 of this plan moves or removes it.";
  progressReply = response({ detail: reason }, 409);
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Start the finish task for order 0a1b2c3d" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(reason);
});
