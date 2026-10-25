"use client";

import { useEffect, useState } from "react";
import { api, type TradeData } from "@/lib/api";

function formatUSD(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

export default function TradesPage() {
  const [trades, setTrades] = useState<TradeData[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setTrades(await api.getTrades(200));
        setError(null);
      } catch {
        setError("Cannot connect to bot API.");
      }
    }
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, []);

  const wins = trades.filter((t) => t.pnl > 0).length;
  const losses = trades.filter((t) => t.pnl < 0).length;
  const winRate = trades.length > 0 ? ((wins / trades.length) * 100).toFixed(1) : "--";

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Trade History</h2>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {error}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">Total Trades</p>
          <p className="text-2xl font-bold font-mono mt-1">{trades.length}</p>
        </div>
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">Win Rate</p>
          <p className="text-2xl font-bold font-mono mt-1">{winRate}%</p>
        </div>
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">W / L</p>
          <p className="text-2xl font-bold font-mono mt-1">
            <span className="text-accent">{wins}</span>
            {" / "}
            <span className="text-accent-red">{losses}</span>
          </p>
        </div>
      </div>

      {/* Trade table */}
      <div className="bg-card border border-card-border rounded-xl p-4">
        {trades.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left border-b border-card-border">
                  <th className="pb-2 font-medium">Time</th>
                  <th className="pb-2 font-medium">Market</th>
                  <th className="pb-2 font-medium">Exchange</th>
                  <th className="pb-2 font-medium">Side</th>
                  <th className="pb-2 font-medium">Outcome</th>
                  <th className="pb-2 font-medium text-right">Price</th>
                  <th className="pb-2 font-medium text-right">Size</th>
                  <th className="pb-2 font-medium text-right">P&L</th>
                  <th className="pb-2 font-medium">Strategy</th>
                  <th className="pb-2 font-medium">Agent</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-card-border/50">
                    <td className="py-2 text-muted text-xs font-mono">
                      {new Date(t.created_at).toLocaleString()}
                    </td>
                    <td className="py-2 max-w-[150px] truncate">{t.market_id}</td>
                    <td className="py-2 text-xs">{t.exchange}</td>
                    <td className={`py-2 ${t.side === "buy" ? "text-accent" : "text-accent-red"}`}>
                      {t.side.toUpperCase()}
                    </td>
                    <td className="py-2">{t.outcome}</td>
                    <td className="py-2 text-right font-mono">{(t.price * 100).toFixed(1)}c</td>
                    <td className="py-2 text-right font-mono">{t.size.toFixed(2)}</td>
                    <td
                      className={`py-2 text-right font-mono ${
                        t.pnl >= 0 ? "text-accent" : "text-accent-red"
                      }`}
                    >
                      {formatUSD(t.pnl)}
                    </td>
                    <td className="py-2 text-xs text-muted">{t.strategy}</td>
                    <td className="py-2 text-xs text-accent-blue">{t.agent_id || "core_engine"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-muted text-sm">No trades yet.</p>
        )}
      </div>
    </div>
  );
}
