import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "../App";

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const post = (change: Record<string, unknown> = {}) => ({
  id: "p1",
  version: 1,
  platform: "Instagram",
  body: "Fresh this week: Vanilla cupcake from $3.93.",
  scheduledAt: "2031-03-04T00:00:00+00:00",
  inCalendar: false,
  optedIn: false,
  approval: "Pending",
  planCurrent: true,
  publication: null,
  ...change,
});
const campaign = (change: Record<string, unknown> = {}) => ({
  id: "c1",
  version: 1,
  objective: "Bring in more cupcake orders.",
  rationale: "Mock plan: features what sold most.",
  startsAt: "2031-03-03T01:00:00+00:00",
  endsAt: "2031-03-09T10:00:00+00:00",
  lowConfidenceNotice: null,
  approval: "Approved",
  products: [{ id: "x", name: "Vanilla cupcake", indicativePrice: "3.93" }],
  posts: [],
  ...change,
});
const summary = {
  historyStart: "2030-12-01T00:00:00+00:00",
  historyEnd: "2031-03-01T00:00:00+00:00",
  overall: { income: "94.23", expenses: "0.00", profit: "94.23", margin: "1.0000", transactions: 1 },
  topProducts: [{ id: "x", name: "Vanilla cupcake", unitsSold: 24 }],
  freeMinutes: 750,
  sufficient: false,
};

let fetchMock: ReturnType<typeof vi.fn>;
let plans: unknown[];
let feed: unknown[];
let replies: Record<string, Response>;

beforeEach(() => {
  location.hash = "#/owner/marketing";
  plans = [];
  feed = [];
  replies = {};
  fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    if (path === "/api/session") return response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" });
    if (path === "/api/marketing/campaigns" && init?.method !== "POST") return response({ campaigns: plans });
    if (path === "/api/marketing/feed") return response({ posts: feed });
    if (path === "/api/marketing/summary") return response(summary);
    return replies[url] ?? response({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

const sent = (suffix: string) =>
  fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith(suffix) && init?.method === "POST");

it("asks for a plan for the chosen dates and shows what it will use", async () => {
  replies["/api/marketing/campaigns"] = response({ id: "c1", version: 1, lowConfidence: true }, 201);
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByText("No campaigns yet")).toBeInTheDocument();
  expect(await screen.findByText("Vanilla cupcake: 24 sold")).toBeInTheDocument();
  expect(screen.getByText(/12 h 30 min/)).toBeInTheDocument();

  const first = screen.getByLabelText("First day");
  const last = screen.getByLabelText("Last day");
  await user.clear(first);
  await user.type(first, "2031-03-03");
  await user.clear(last);
  await user.type(last, "2031-03-01");
  expect(screen.getByRole("button", { name: "Ask for a plan" })).toBeDisabled();
  await user.clear(last);
  await user.type(last, "2031-03-09");

  await user.click(screen.getByRole("button", { name: "Ask for a plan" }));

  expect(await screen.findByText(/waiting for your approval/)).toBeInTheDocument();
  const [, init] = sent("/api/marketing/campaigns")[0];
  expect(JSON.parse(String(init.body))).toEqual({ planningStart: "2031-03-03", planningEnd: "2031-03-09" });
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("t");
});

it("offers no post until the plan is approved, and approves that version", async () => {
  plans = [campaign({ approval: "Pending", version: 2, lowConfidenceNotice: "Planned from limited business data." })];
  replies["/api/marketing/campaigns/c1/versions/2/approve"] = response({ id: "c1", version: 2, approval: "Approved" });
  const user = userEvent.setup();
  render(<App />);
  const card = (await screen.findByRole("heading", { name: "Bring in more cupcake orders." })).closest("section")!;
  expect(within(card).getByText("Planned from limited business data.")).toBeInTheDocument();
  expect(within(card).getByText("Vanilla cupcake · from $3.93")).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Write a post" })).not.toBeInTheDocument();

  await user.click(within(card).getByRole("button", { name: "Approve version 2" }));

  expect(sent("/versions/2/approve")).toHaveLength(1);
});

it("lines up new plans to compare and folds rejected and finished ones away", async () => {
  plans = [
    campaign({ id: "a", approval: "Pending", objective: "Plan A." }),
    campaign({ id: "b", approval: "Pending", objective: "Plan B." }),
    campaign({ id: "c", objective: "Running plan." }),
    campaign({ id: "d", approval: "Rejected", objective: "Rejected plan." }),
    campaign({ id: "e", objective: "Last month’s plan.", startsAt: "2026-01-01T01:00:00+00:00", endsAt: "2026-01-07T10:00:00+00:00" }),
  ];
  replies["/api/marketing/campaigns"] = response({ id: "f", version: 1, lowConfidence: false }, 201);
  const user = userEvent.setup();
  render(<App />);

  const compare = (await screen.findByRole("heading", { name: "2 plans to compare" })).closest("section")!;
  expect(within(compare).getByRole("heading", { name: "Plan A." })).toBeInTheDocument();
  expect(within(compare).getByRole("heading", { name: "Plan B." })).toBeInTheDocument();
  // Another plan is already approved, so each is marked; nothing is rejected for the owner.
  expect(within(compare).getAllByText(/“Running plan\.” is already approved/)).toHaveLength(2);
  expect(within(compare).getAllByRole("button", { name: /Approve version/ })).toHaveLength(2);
  expect(within(compare).queryByText("Running plan.")).not.toBeInTheDocument();

  const earlier = screen.getByText(/Earlier plans/).closest("details")!;
  expect(earlier).not.toHaveAttribute("open");
  expect(within(earlier).getByText("2")).toBeInTheDocument();
  expect(within(earlier).getByText("Rejected plan.")).toBeInTheDocument();
  expect(within(earlier).getByText("Last month’s plan.")).toBeInTheDocument();
  // A finished campaign takes no new posts.
  expect(within(earlier).queryByRole("button", { name: "Write a post" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Write a post" })).toHaveLength(1);

  await user.click(within(compare).getByRole("button", { name: "Ask for another plan" }));

  expect(await screen.findByText(/Another plan is ready/)).toBeInTheDocument();
  expect(sent("/api/marketing/campaigns")).toHaveLength(1);
});

it("writes a post for the chosen platform and time", async () => {
  plans = [campaign()];
  replies["/api/marketing/campaigns/c1/versions/1/content"] = response({ id: "p1", version: 1 }, 201);
  const user = userEvent.setup();
  render(<App />);
  await user.selectOptions(await screen.findByLabelText("Platform"), "Facebook");
  const time = screen.getByLabelText("Post time");
  await user.clear(time);
  await user.type(time, "2031-03-05T09:30");

  await user.click(screen.getByRole("button", { name: "Write a post" }));

  await waitFor(() => expect(sent("/content")).toHaveLength(1));
  expect(JSON.parse(String(sent("/content")[0][1].body))).toEqual({
    platform: "Facebook",
    scheduledAt: new Date("2031-03-05T09:30").toISOString(),
  });
});

it("offers each post only its next step", async () => {
  plans = [campaign({ posts: [post()] })];
  const { unmount } = render(<App />);
  expect(await screen.findByRole("button", { name: "Approve post" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish now" })).not.toBeInTheDocument();
  unmount();

  plans = [campaign({ posts: [post({ approval: "Approved" })] })];
  const second = render(<App />);
  expect(await screen.findByRole("button", { name: "Add to calendar" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve post" })).not.toBeInTheDocument();
  second.unmount();

  plans = [campaign({ posts: [post({ approval: "Approved", inCalendar: true })] })];
  const third = render(<App />);
  // Publishing is opt-in, per post.
  expect(await screen.findByRole("button", { name: "Publish now" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Allow this post to be published" })).not.toBeChecked();
  third.unmount();

  plans = [campaign({ posts: [post({
    approval: "Approved", inCalendar: true, optedIn: true,
    publication: { status: "Published", reference: "mocksocial-1", publishedAt: "2031-03-04T00:00:00+00:00", error: null },
  })] })];
  render(<App />);
  expect(await screen.findByText(/as mocksocial-1/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish now" })).not.toBeInTheDocument();
});

it("labels every post and folds rejected and outdated ones under the campaign", async () => {
  const body = (text: string) => ({ body: text });
  plans = [campaign({ posts: [
    post({ id: "p1", ...body("Waiting post.") }),
    post({ id: "p2", approval: "Approved", inCalendar: true, ...body("Calendar post.") }),
    post({ id: "p3", approval: "Rejected", ...body("Rejected post.") }),
    post({ id: "p4", approval: "Approved", inCalendar: true, planCurrent: false, ...body("Outdated post.") }),
    post({ id: "p5", approval: "Approved", inCalendar: true, optedIn: true, planCurrent: false, ...body("Published post."),
      publication: { status: "Published", reference: "mocksocial-1", publishedAt: "2031-03-04T00:00:00+00:00", error: null } }),
  ] })];
  render(<App />);
  const label = async (text: string) => (await screen.findByText(text)).closest("article")!.querySelector(".badge");
  expect(await label("Waiting post.")).toHaveTextContent("Waiting for you");
  expect(await label("Calendar post.")).toHaveTextContent("In the calendar");
  expect(await label("Published post.")).toHaveTextContent("Published");

  const earlier = screen.getByText(/Earlier posts/).closest("details")!;
  expect(earlier).not.toHaveAttribute("open");
  expect(within(earlier).getByText("2")).toBeInTheDocument();
  expect(within(earlier).getByText("Rejected post.")).toBeInTheDocument();
  expect(within(earlier).getByText("Outdated post.")).toBeInTheDocument();
  expect(await label("Rejected post.")).toHaveTextContent("Rejected");
  expect(await label("Outdated post.")).toHaveTextContent("Plan changed");
  // A post that already went out stays in view, whatever happened to the plan since.
  expect(within(earlier).queryByText("Published post.")).not.toBeInTheDocument();
});

it("turns publishing on, then publishes that version", async () => {
  plans = [campaign({ posts: [post({ approval: "Approved", inCalendar: true, version: 3 })] })];
  replies["/api/marketing/content/p1/versions/3/opt-in"] = response({ id: "p1", version: 3, optedIn: true });
  replies["/api/marketing/content/p1/versions/3/publish"] = response({ id: "p1", version: 3, status: "Published", reference: "mocksocial-1" });
  const user = userEvent.setup();
  render(<App />);

  await user.click(await screen.findByRole("checkbox", { name: "Allow this post to be published" }));
  await waitFor(() => expect(sent("/opt-in")).toHaveLength(1));
  expect(JSON.parse(String(sent("/opt-in")[0][1].body))).toEqual({ optedIn: true });

  plans = [campaign({ posts: [post({ approval: "Approved", inCalendar: true, version: 3, optedIn: true })] })];
  await user.click(await screen.findByRole("checkbox", { name: "Allow this post to be published" }));
  await waitFor(() => expect(sent("/opt-in")).toHaveLength(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Publish now" })).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "Publish now" }));
  await waitFor(() => expect(sent("/versions/3/publish")).toHaveLength(1));
});

it("shows the server's reason on the post it refused, and keeps the post", async () => {
  plans = [campaign({ posts: [post({ approval: "Approved", inCalendar: true, optedIn: true })] })];
  replies["/api/marketing/content/p1/versions/1/publish"] = response(
    { detail: "The channel refused this post, so nothing was published.", status: "ChannelFailed", problems: [] }, 503);
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Publish now" }));

  const article = screen.getByRole("article", { name: /Post: Instagram/ });
  expect(await within(article).findByText(/channel refused this post/)).toBeInTheDocument();
  expect(within(article).getByText(/Fresh this week/)).toBeInTheDocument();
});

it("hands over the post's own text only after the server allows the export", async () => {
  plans = [campaign({ posts: [post({ approval: "Approved", inCalendar: true })] })];
  replies["/api/marketing/content/p1/versions/1/export"] = response({ id: "p1", version: 1, text: "[Instagram | 2031-03-04T00:00:00+00:00]\nFresh" });
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Post it by hand" }));

  const text = await screen.findByLabelText(/Text for Instagram/);
  expect(text).toHaveValue("Fresh this week: Vanilla cupcake from $3.93.");
  // The heading with the UTC time is not part of what the owner posts.
  expect(screen.queryByDisplayValue(/\[Instagram/)).not.toBeInTheDocument();
});

it("explains a post whose plan has changed, and rewrites it once the new plan is approved", async () => {
  plans = [campaign({ version: 2, approval: "Pending", posts: [post({ approval: "Approved", inCalendar: true, planCurrent: false })] })];
  const { unmount } = render(<App />);
  expect(await screen.findByText(/Approve the new plan, then write the post again/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Write it again" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish now" })).not.toBeInTheDocument();
  unmount();

  plans = [campaign({ version: 2, posts: [post({ approval: "Approved", inCalendar: true, planCurrent: false })] })];
  replies["/api/marketing/content/p1/versions/1/revise"] = response({ id: "p1", version: 2 }, 201);
  const user = userEvent.setup();
  render(<App />);
  // A post for an older plan is folded away with the rejected ones.
  await user.click(await screen.findByText(/Earlier posts/));
  await user.click(screen.getByRole("button", { name: "Write it again" }));
  await waitFor(() => expect(sent("/versions/1/revise")).toHaveLength(1));
  expect(JSON.parse(String(sent("/versions/1/revise")[0][1].body))).toEqual({
    platform: "Instagram", scheduledAt: "2031-03-04T00:00:00+00:00",
  });
});

it("shows the published feed and reports when nothing is due", async () => {
  feed = [{ reference: "mocksocial-1", channel: "MockSocial", platform: "Facebook", body: "Fresh cakes.", publishedAt: "2031-03-04T00:00:00+00:00" }];
  replies["/api/marketing/publish-due"] = response({ published: [], failed: [] });
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByText("Fresh cakes.")).toBeInTheDocument();
  expect(screen.getByText(/local mock channel/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Publish what’s due" }));

  expect(await screen.findByText(/Nothing is due/)).toBeInTheDocument();
});

it("counts the plans and posts waiting on the owner in the sidebar", async () => {
  plans = [campaign({ approval: "Pending" }), campaign({ id: "c2", posts: [post(), post({ id: "p2", approval: "Approved" })] })];
  render(<App />);
  const link = await screen.findByRole("link", { name: "Marketing" });
  await waitFor(() => expect(link).toHaveAttribute("aria-description", "2 waiting"));
});

it("tells the owner when marketing cannot be loaded", async () => {
  fetchMock.mockImplementation(async (url: string) =>
    url === "/api/session"
      ? response({ user: { displayName: "Owner", role: "Owner" }, mode: "mock", csrfToken: "t" })
      : response({ detail: "Marketing could not be loaded or saved." }, 503));
  render(<App />);
  expect(await screen.findByText("Marketing could not be loaded or saved.")).toBeInTheDocument();
});
