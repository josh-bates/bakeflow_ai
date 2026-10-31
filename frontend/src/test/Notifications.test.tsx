import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Notifications } from "../customer/Notifications";

const update = { id: "event-1", orderId: "order-1", status: "Quoted", message: "Order order-1: Review your quotation.", createdAt: "2026-10-31T01:00:00Z" };
const response = (notifications: unknown[], status = 200) => new Response(JSON.stringify({ notifications }), { status });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("loads privately and refreshes automatically without duplicating replayed updates", async () => {
  const fetch = vi.fn().mockResolvedValue(response([]));
  vi.stubGlobal("fetch", fetch);
  vi.useFakeTimers();
  const view = render(<Notifications refresh={0} onExpired={() => {}} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText(/No updates yet/)).toBeInTheDocument();
  fetch.mockImplementation(async () => response([update]));
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(screen.getByText(update.message)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(screen.getAllByText(update.message)).toHaveLength(1);
  expect(fetch).toHaveBeenCalledWith("/api/notifications", expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
  view.unmount();
  const calls = fetch.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(fetch).toHaveBeenCalledTimes(calls);
});

it("offers retry on failure and refreshes when a draft is saved", async () => {
  const fetch = vi.fn().mockResolvedValue(response([], 503));
  vi.stubGlobal("fetch", fetch);
  const expired = vi.fn();
  const view = render(<Notifications refresh={0} onExpired={expired} />);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  fetch.mockImplementation(async () => response([update]));
  fireEvent.click(screen.getByRole("button", { name: "Retry updates" }));
  expect(await screen.findByText(update.message)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  view.rerender(<Notifications refresh={1} onExpired={expired} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
});

it("expires the session and ignores a response arriving after unmount", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([], 401)));
  const expired = vi.fn();
  const view = render(<Notifications refresh={0} onExpired={expired} />);
  await waitFor(() => expect(expired).toHaveBeenCalledTimes(1));
  view.unmount();
  let finish!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
  const later = render(<Notifications refresh={0} onExpired={expired} />);
  later.unmount();
  await act(async () => { finish(response([], 401)); });
  expect(expired).toHaveBeenCalledTimes(1);
});
