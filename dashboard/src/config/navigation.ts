export type DashboardNavGroup = "core" | "analysis" | "operations";

export interface DashboardNavItem {
  href: string;
  label: string;
  description: string;
  group: DashboardNavGroup;
}

export const dashboardNavItems: DashboardNavItem[] = [
  {
    href: "/",
    label: "Overview",
    description: "Portfolio and build readiness",
    group: "core",
  },
  {
    href: "/positions",
    label: "Positions",
    description: "Live open position monitoring",
    group: "core",
  },
  {
    href: "/trades",
    label: "Trades",
    description: "Execution history and outcomes",
    group: "core",
  },
  {
    href: "/signals",
    label: "Signals",
    description: "Decision feed and confidence",
    group: "analysis",
  },
  {
    href: "/agents",
    label: "Agent Orchestrator",
    description: "Claude agent runtime and learning flow",
    group: "analysis",
  },
  {
    href: "/markets",
    label: "Markets",
    description: "Watchlist and market discovery",
    group: "analysis",
  },
  {
    href: "/copy",
    label: "Copy Trading",
    description: "Polymarket leader wallet mirroring",
    group: "core",
  },
  {
    href: "/graph",
    label: "Knowledge Graph",
    description: "Entity and relationship explorer",
    group: "analysis",
  },
  {
    href: "/simulations",
    label: "Simulations",
    description: "Swarm scenario testing",
    group: "operations",
  },
  {
    href: "/accuracy",
    label: "Learning Loop",
    description: "Model calibration and weight updates",
    group: "operations",
  },
  {
    href: "/logs",
    label: "Live Logs",
    description: "Realtime system output stream",
    group: "operations",
  },
];
