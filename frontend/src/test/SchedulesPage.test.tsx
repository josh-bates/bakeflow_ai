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
let fetchMock: ReturnType<typeof vi.fn>;
let schedules: unknown[];
beforeEach(() => {
  location.hash = "#/owner/schedules";
  schedules = [awaiting];
  fetchMock = vi.fn().mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "csrf-1" })
      : url === "/api/schedules"
        ? response({ timeZone: "Australia/Sydney", schedules })
        : url === `/api/schedules/${ID}/decision`
          ? response(blocked)
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
