import type { Session } from "../api/client";
import { AIActivityPage } from "./AIActivityPage";
import { ApprovalsPage } from "./ApprovalsPage";
import { FinancePage } from "./FinancePage";
import { InventoryPage } from "./InventoryPage";
import { SchedulesPage } from "./SchedulesPage";

export const ownerPages = {
  approvals: {
    title: "Pending approvals",
    description: "Review the decisions that need your attention.",
    detail: "",
  },
  orders: {
    title: "Orders",
    description: "A home for every customer occasion.",
    detail:
      "Customer orders and their current status will appear here once the owner order service is connected.",
  },
  inventory: {
    title: "Inventory",
    description: "Know what’s on hand before you start.",
    detail: "",
  },
  finance: {
    title: "Finance",
    description: "Profit from your recorded income and expenses.",
    detail: "",
  },
  schedules: {
    title: "Schedules",
    description: "Make room for a well-planned baking day.",
    detail: "",
  },
  ai: {
    title: "AI activity",
    description: "Every assistant request, with its retries and result.",
    detail: "",
  },
};
export type OwnerPageName = keyof typeof ownerPages;

export function OwnerPage({ page, session }: { page: OwnerPageName; session: Session }) {
  if (page === "approvals") return <ApprovalsPage session={session} />;
  if (page === "finance") return <FinancePage />;
  if (page === "inventory") return <InventoryPage session={session} />;
  if (page === "schedules") return <SchedulesPage session={session} />;
  if (page === "ai") return <AIActivityPage />;
  const content = ownerPages[page];
  return (
    <>
      <section className="intro owner-intro">
        <img className="hero-art" src="/images/chocolate-cake.jpg" alt="" aria-hidden="true" />
        <p className="eyebrow">OWNER WORKSPACE</p>
        <h1>{content.title}</h1>
        <p>{content.description}</p>
      </section>
      <section className="card placeholder">
        <span className="stamp" aria-hidden="true">
          ◇
        </span>
        <span className="badge">Coming soon</span>
        <h2>Your workspace is taking shape</h2>
        <p>{content.detail}</p>
        <div className="notice">
          This page is a placeholder. No live records or actions are available
          here yet.
        </div>
      </section>
    </>
  );
}
