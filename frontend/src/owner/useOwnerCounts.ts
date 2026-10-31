import { useEffect, useRef, useState } from "react";
import { api, DATA_CHANGED, type QuoteQueueItem } from "../api/client";
import { changes } from "../api/orderChanges";
import { referrals } from "../api/referrals";
import type { OwnerPageName } from "./OwnerPage";

type Counts = Partial<Record<OwnerPageName, number>>;

// Accepted quotes and quotes still with the customer are nobody's job on this page.
const needsOwner = ({ quote, ready, approval }: QuoteQueueItem) =>
  !quote ? ready : approval !== "Approved" || quote.customerDecision === "Rejected";

// A change request is the owner's to act on until a revised quote goes to the customer.
const OWNER_TURN = ["Pending", "AwaitingApproval"];

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
        referrals.list(),
        changes.list(true),
        api.marketingCampaigns(),
      ]).then(([quotes, stock, schedules, reviews, requests, campaigns]) => {
        if (request !== latest.current) return;
        // The approvals page holds both quotes and order exceptions waiting for a resolution.
        const quoting = count(quotes, needsOwner);
        const reviewing = count(reviews, (review) => review.status === "Pending");
        setCounts({
          approvals: quoting === undefined && reviewing === undefined ? undefined : (quoting ?? 0) + (reviewing ?? 0),
          orders: count(requests, (change) => OWNER_TURN.includes(change.status)),
          inventory: count(stock, (item) => item.low),
          schedules: count(schedules, (schedule) => schedule.state === "AwaitingApproval"),
          marketing: campaigns.status === "fulfilled"
            ? campaigns.value.flatMap((c) => [c, ...c.posts]).filter((item) => item.approval === "Pending").length
            : undefined,
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
