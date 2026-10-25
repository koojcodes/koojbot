"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { dashboardNavItems, type DashboardNavGroup } from "@/config/navigation";

const groupLabels: Record<DashboardNavGroup, string> = {
  core: "Core",
  analysis: "Analysis",
  operations: "Operations",
};

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <div className="flex flex-col gap-4 p-3 flex-1 overflow-y-auto">
      {Object.entries(groupLabels).map(([group, label]) => {
        const items = dashboardNavItems.filter((item) => item.group === group);
        if (items.length === 0) return null;

        return (
          <section key={group}>
            <p className="px-3 mb-2 text-[11px] tracking-[0.12em] uppercase text-muted">
              {label}
            </p>
            <div className="space-y-1">
              {items.map((item) => {
                const isActive = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`block rounded-lg border px-3 py-2 transition-colors ${
                      isActive
                        ? "border-accent/35 bg-accent/10"
                        : "border-transparent text-muted hover:text-foreground hover:bg-card hover:border-card-border"
                    }`}
                  >
                    <p
                      className={`text-sm font-medium ${
                        isActive ? "text-foreground" : ""
                      }`}
                    >
                      {item.label}
                    </p>
                    <p className="text-xs text-muted">{item.description}</p>
                  </Link>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
