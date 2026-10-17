# BakeFlow AI

An AI-assisted back-office system for a home-based bakery, developed for **ELEC5620 — Model-Based Software Engineering, University of Sydney**.

BakeFlow AI helps a bakery owner manage custom cake and cupcake enquiries, orders, ingredient stock, production, quotations, bookkeeping and marketing. Four specialist AI agents interpret requests and propose outcomes; deterministic services validate business data, perform calculations and coordinate workflows. The owner retains approval over important decisions.

**Status:** Stage Two repository preparation only. This initial snapshot contains documentation and reference materials. Application code, dependencies, deployment configuration and automated workflows have not been implemented. The framework, SQL database engine and LLM provider remain undecided, as specified in Stage One.

## Planned capabilities

| Module | Stage One requirements | Use cases | Stage One lead |
| --- | --- | --- | --- |
| Sales and Customer Service | AR-01 | UC-01: submit a custom order; UC-02: track, change or cancel an order | Ziyi Wang |
| Inventory and Production | AR-02, IP-01–IP-05 | UC-03: check inventory and create a shopping list; UC-04: create and approve a production schedule | Chuanyang Wang |
| Pricing and Bookkeeping | AR-03 | UC-05: generate and approve a quotation; UC-06: record transactions and review profitability | Joshua Bates |
| Marketing | AR-04 | UC-07: plan a campaign; UC-08: generate and approve marketing content | Angelina |

These are the report's design responsibilities, not a claim about completed implementation or future contribution percentages.

## Project references

- [Stage One design baseline](docs/design-baseline.md)
- [Stage Two assessment and evidence](docs/stage-two.md)
- [Jira handoff and initial backlog](docs/jira-handoff.md)
- [Contribution workflow](CONTRIBUTING.md)
- [Source material inventory](docs/source-materials.md)
- [Original Stage One report](docs/reference/stage-1-report.pdf)
- [Original Stage Two marking criteria](docs/reference/stage-2-marking-criteria.pdf)

Jira will hold the live backlog, sprint commitments, estimates and collaboration evidence. GitHub will hold version history and reviewed changes. No Jira project, sprint or integration is configured in this snapshot.

## Scope

The prototype uses simulated business data, browser interfaces, one application host, a shared SQL database and a server-side gateway to an external LLM. Payment execution, ingredient purchasing, physical baking, delivery routing and automatic social-media publication are outside the automated prototype. Approved marketing content is exported for manual publication.

## Repository setup

GitHub repository: [josh-bates/bakeflow_ai](https://github.com/josh-bates/bakeflow_ai), created as **private**. Display name: **BakeFlow AI**. GitHub description:

> ELEC5620 Stage Two: AI-assisted bakery operations with four specialist agents, deterministic workflows and owner approvals.

See [repository setup and Jira handoff](docs/repository-setup.md).
