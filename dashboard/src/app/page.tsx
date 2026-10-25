"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MetricCard } from "@/components/metrics/MetricCard";
import { EquityCurve } from "@/components/charts/EquityCurve";
import { api, type PortfolioState, type PortfolioSnapshot } from "@/lib/api";
import { dashboardNavItems } from "@/config/navigation";

function formatUSD(n: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(n);
}

function formatPct(n: number) {
  return `${(n * 100).toFixed(2)}%`;
}

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null);
  const [history, setHistory] = useState<PortfolioSnapshot[]>([]);
  const [botOnline, setBotOnline] = useState<boolean | null>(null);
  const [paperMode, setPaperMode] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [p, h, health] = await Promise.all([
          api.getPortfolio(),
          api.getPortfolioHistory(),
          api.getHealth(),
        ]);
        setPortfolio(p);
        setHistory(h);
        setBotOnline(true);
        setPaperMode(health.paper_mode);
        setError(null);
      } catch {
        setBotOnline(false);
        setPaperMode(null);
        setError("Cannot connect to bot API. Make sure the bot is running with --api flag.");
      }
    }
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  // Build equity curve and always include current state as latest point.
  const equityData = (() => {
    const points = history
      .slice()
      .reverse()
      .map((s) => ({
        time: s.created_at,
        value: s.balance + s.total_exposure + s.unrealized_pnl,
      }));
    // Always append current state as the most recent point so chart is never empty
    if (portfolio) {
      points.push({ time: portfolio.timestamp, value: portfolio.total_value });
    }
    return points;
  })();

  const checklist = [
    {
      item: "Bot API connectivity",
      ready: botOnline === true,
      pending: botOnline === null,
      help: "Start the bot with API enabled and verify /api/health.",
    },
    {
      item: "Portfolio feed",
      ready: portfolio !== null,
      pending: botOnline === null,
      help: "Confirm /api/portfolio and /api/portfolio/history return data.",
    },
    {
      item: "Interactive modules",
      ready: true,
      pending: false,
      help: "Navigation and route structure are now normalized.",
    },
  ];

  return (
    <div className="space-y-6">
      <section className="bg-card border border-card-border rounded-xl p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold">Rebuild Readiness</h3>
            <p className="text-sm text-muted mt-1">
              This workspace is cleaned and structured for the next interactive dashboard iteration.
            </p>
          </div>
          <span
            className={`text-xs px-2.5 py-1 rounded border ${
              botOnline
                ? "border-accent/40 bg-accent/10 text-accent"
                : "border-accent-red/40 bg-accent-red/10 text-accent-red"
            }`}
          >
            {botOnline ? "API Online" : "API Offline"}
          </span>
        </div>

        <div className="mt-4 grid md:grid-cols-3 gap-3">
          {checklist.map((entry) => (
            <div key={entry.item} className="rounded-lg border border-card-border bg-surface p-3">
              <p className="text-sm font-medium">{entry.item}</p>
              <p
                className={`text-xs mt-1 ${
                  entry.pending
                    ? "text-accent-amber"
                    : entry.ready
                    ? "text-accent"
                    : "text-accent-red"
                }`}
              >
                {entry.pending ? "Checking..." : entry.ready ? "Ready" : "Needs attention"}
              </p>
              <p className="text-xs text-muted mt-2">{entry.help}</p>
            </div>
          ))}
        </div>
      </section>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Total Value"
          value={portfolio ? formatUSD(portfolio.total_value) : "--"}
        />
        <MetricCard
          label="Balance"
          value={portfolio ? formatUSD(portfolio.balance) : "--"}
        />
        <MetricCard
          label="Unrealized P&L"
          value={portfolio ? formatUSD(portfolio.unrealized_pnl) : "--"}
          change={portfolio ? formatUSD(portfolio.unrealized_pnl) : undefined}
          positive={portfolio ? portfolio.unrealized_pnl >= 0 : undefined}
        />
        <MetricCard
          label="Realized P&L"
          value={portfolio ? formatUSD(portfolio.realized_pnl) : "--"}
          change={portfolio ? formatUSD(portfolio.realized_pnl) : undefined}
          positive={portfolio ? portfolio.realized_pnl >= 0 : undefined}
        />
      </div>

      <section className="bg-card border border-card-border rounded-xl p-4">
        <h3 className="text-sm text-muted mb-4 uppercase tracking-wide">Equity Curve</h3>
        {equityData.length > 0 ? (
          <EquityCurve data={equityData} height={350} />
        ) : (
          <div className="h-[350px] flex items-center justify-center text-muted text-sm">
            No data yet. Start the bot to see your equity curve.
          </div>
        )}
      </section>

      <section className="bg-card border border-card-border rounded-xl p-4">
        <h3 className="text-sm text-muted mb-3 uppercase tracking-wide">
          Dashboard Modules
        </h3>
        <p className="text-sm text-muted mb-4">
          Use these routes as the initial module set while we build the fully interactive dashboard.
        </p>
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {dashboardNavItems
            .filter((item) => item.href !== "/")
            .map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-lg border border-card-border bg-surface px-4 py-3 hover:border-accent/35 hover:bg-accent/5 transition-colors"
              >
                <p className="text-sm font-medium">{item.label}</p>
                <p className="text-xs text-muted mt-1">{item.description}</p>
              </Link>
            ))}
        </div>
      </section>

      <section className="bg-card border border-card-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm text-muted uppercase tracking-wide">
            Active Positions ({portfolio?.num_positions ?? 0})
          </h3>
          <p className="text-xs text-muted">
            Runtime: {paperMode === null ? "Unknown" : paperMode ? "Paper" : "Live"}
          </p>
        </div>
        {portfolio && portfolio.positions.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left border-b border-card-border">
                  <th className="pb-2 font-medium">Market</th>
                  <th className="pb-2 font-medium">Side</th>
                  <th className="pb-2 font-medium text-right">Size</th>
                  <th className="pb-2 font-medium text-right">Entry</th>
                  <th className="pb-2 font-medium text-right">Current</th>
                  <th className="pb-2 font-medium text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.positions.map((p, i) => (
                  <tr key={i} className="border-b border-card-border/50">
                    <td className="py-2 max-w-[200px] truncate">
                      {p.market_title || p.market_id}
                    </td>
                    <td className="py-2">{p.outcome}</td>
                    <td className="py-2 text-right font-mono">{p.size.toFixed(2)}</td>
                    <td className="py-2 text-right font-mono">
                      {formatPct(p.avg_entry_price)}
                    </td>
                    <td className="py-2 text-right font-mono">
                      {formatPct(p.current_price)}
                    </td>
                    <td
                      className={`py-2 text-right font-mono ${
                        p.unrealized_pnl >= 0 ? "text-accent" : "text-accent-red"
                      }`}
                    >
                      {formatUSD(p.unrealized_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-muted text-sm">No open positions.</p>
        )}
      </section>
    </div>
  );
}
