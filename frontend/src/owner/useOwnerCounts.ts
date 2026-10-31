import { useEffect, useRef, useState } from "react";
import { api, DATA_CHANGED, type QuoteQueueItem } from "../api/client";
import type { OwnerPageName } from "./OwnerPage";

type Counts = Partial<Record<OwnerPageName, number>>;

// Accepted quotes and quotes still with the customer are nobody's job on this page.
const needsOwner = ({ quote, ready, approval }: QuoteQueueItem) =>
  !quote ? ready : approval !== "Approved" || quote.customerDecision === "Rejected";

const count = <T,>(result: PromiseSettledResult<T[]>, waiting: (item: T) => boolean) =>
  result.status === "fulfilled" ? result.value.filter(waiting).length : undefined;

// The sidebar's counts of what is waiting on each page. They refresh on every page
// change and after anything is saved; one that cannot load is simply not shown.
export function useOwnerCounts(enabled: boolean, route: string): Counts {
  const [counts, setCounts] = useState<Counts>({});
  const latest = useRef(0);
  useEffect(() => {
    if (!enabled) {
      setCounts({});
      return;
    }
    const load = () => {
      const request = ++latest.current;
      Promise.allSettled([
        api.quoteQueue(),
        api.stock(),
        api.schedules().then((data) => data.schedules),
      ]).then(([quotes, stock, schedules]) => {
        if (request !== latest.current) return;
        setCounts({
          approvals: count(quotes, needsOwner),
          inventory: count(stock, (item) => item.low),
          schedules: count(schedules, (schedule) => schedule.state === "AwaitingApproval"),
        });
      });
    };
    load();
    window.addEventListener(DATA_CHANGED, load);
    return () => {
      latest.current++;
      window.removeEventListener(DATA_CHANGED, load);
    };
  }, [enabled, route]);
  return counts;
}
