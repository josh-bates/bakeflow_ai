export const ownerPages = {
  approvals: {
    title: "Pending approvals",
    description: "Review the decisions that need your attention.",
    detail:
      "Quotations, production plans and marketing content will appear here for review of their current version.",
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
    detail:
      "Ingredient availability and shortage lists will appear here. Stock calculations are handled by the backend.",
  },
  schedules: {
    title: "Schedules",
    description: "Make room for a well-planned baking day.",
    detail:
      "Production plans and their approval status will appear here once the scheduling service is connected.",
  },
};
export type OwnerPageName = keyof typeof ownerPages;

export function OwnerPage({ page }: { page: OwnerPageName }) {
  const content = ownerPages[page];
  return (
    <>
      <section className="intro">
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
