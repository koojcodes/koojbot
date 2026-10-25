"use client";

import { useEffect, useState } from "react";
import { api, type PositionData } from "@/lib/api";

function formatUSD(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

function formatPct(n: number) {
  return `${(n * 100).toFixed(2)}%`;
}

export default function PositionsPage() {
  const [positions, setPositions] = useState<PositionData[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setPositions(await api.getPositions());
        setError(null);
      } catch {
        setError("Cannot connect to bot API.");
      }
    }
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const totalPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Active Positions</h2>
        <div className="text-sm">
          Total P&L:{" "}
          <span className={`font-mono font-bold ${totalPnl >= 0 ? "text-accent" : "text-accent-red"}`}>
            {formatUSD(totalPnl)}
          </span>
        </div>
      </div>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {error}
        </div>
      )}

      {positions.length > 0 ? (
        <div className="grid gap-4">
          {positions.map((p, i) => (
            <div key={i} className="bg-card border border-card-border rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <div>
                  <h3 className="font-medium">{p.market_title || p.market_id}</h3>
                  <p className="text-xs text-muted">
                    {p.exchange} &middot; {p.outcome}
                  </p>
                </div>
                <div
                  className={`text-xl font-bold font-mono ${
                    p.unrealized_pnl >= 0 ? "text-accent" : "text-accent-red"
                  }`}
                >
                  {formatUSD(p.unrealized_pnl)}
                </div>
              </div>
              <div className="grid grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-xs text-muted">Size</p>
                  <p className="font-mono">{p.size.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted">Entry Price</p>
                  <p className="font-mono">{formatPct(p.avg_entry_price)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted">Current Price</p>
                  <p className="font-mono">{formatPct(p.current_price)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted">Return</p>
                  <p
                    className={`font-mono ${
                      p.unrealized_pnl_pct >= 0 ? "text-accent" : "text-accent-red"
                    }`}
                  >
                    {formatPct(p.unrealized_pnl_pct)}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-card border border-card-border rounded-xl p-8 text-center text-muted text-sm">
          No open positions. Start the bot to begin trading.
        </div>
      )}
    </div>
  );
}
