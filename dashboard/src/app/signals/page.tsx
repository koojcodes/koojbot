"use client";

import { useEffect, useState } from "react";
import { api, type SignalData } from "@/lib/api";

function ConvictionBar({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const color =
    pct >= 70
      ? "bg-accent"
      : pct >= 45
      ? "bg-accent-amber"
      : "bg-accent-red";

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-muted-dark rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono tabular-nums w-8 text-right">{pct}%</span>
    </div>
  );
}

function StrategyBadges({ strategy }: { strategy: string }) {
  const s = strategy.toLowerCase();
  const badges = [];

  if (s === "conviction" || s.includes("news")) {
    badges.push(
      <span key="news" className="text-[10px] px-1.5 py-0.5 rounded bg-accent-blue/15 text-accent-blue font-medium">
        📰 News
      </span>
    );
  }
  if (s === "conviction" || s.includes("momentum")) {
    badges.push(
      <span key="momentum" className="text-[10px] px-1.5 py-0.5 rounded bg-accent-amber/15 text-accent-amber font-medium">
        📈 Momentum
      </span>
    );
  }
  if (s === "conviction") {
    badges.push(
      <span key="conviction" className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent font-bold">
        🎯 Both
      </span>
    );
  }
  if (s === "risk_manager") {
    badges.push(
      <span key="risk" className="text-[10px] px-1.5 py-0.5 rounded bg-accent-red/15 text-accent-red font-medium">
        🛑 Risk Exit
      </span>
    );
  }

  return badges.length > 0 ? (
    <div className="flex items-center gap-1">{badges}</div>
  ) : (
    <span className="text-xs text-muted">{strategy}</span>
  );
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<SignalData[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [highConviction, setHighConviction] = useState(0);
  const [recentCount, setRecentCount] = useState(0);

  useEffect(() => {
    async function load() {
      try {
        const nextSignals = await api.getSignals(100);
        const now = Date.now();
        setSignals(nextSignals);
        setHighConviction(nextSignals.filter((s) => s.confidence >= 0.7).length);
        setRecentCount(
          nextSignals.filter((s) => now - new Date(s.timestamp).getTime() < 3600000).length
        );
        setError(null);
      } catch {
        setError("Cannot connect to bot API.");
      }
    }
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Trading Signals</h2>
        {signals.length > 0 && (
          <div className="flex items-center gap-4 text-sm text-muted">
            <span>
              <span className="text-accent font-mono font-bold">{highConviction}</span> high conviction
            </span>
            <span>
              <span className="text-foreground font-mono font-bold">{recentCount}</span> last hour
            </span>
          </div>
        )}
      </div>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {error}
        </div>
      )}

      {signals.length > 0 ? (
        <div className="grid gap-3">
          {signals.map((s, i) => (
            <div
              key={i}
              className={`bg-card border rounded-xl p-4 transition-colors ${
                s.strategy === "conviction"
                  ? "border-accent/20"
                  : "border-card-border"
              }`}
            >
              {/* Header row */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2 min-w-0">
                  {/* Action badge */}
                  <span
                    className={`shrink-0 px-2 py-0.5 rounded text-xs font-bold uppercase ${
                      s.action === "buy"
                        ? "bg-accent/20 text-accent"
                        : s.action === "sell"
                        ? "bg-accent-red/20 text-accent-red"
                        : "bg-muted-dark/40 text-muted"
                    }`}
                  >
                    {s.action}
                  </span>
                  {/* Outcome pill */}
                  <span className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-surface text-muted border border-card-border">
                    {s.outcome}
                  </span>
                  {/* Market title */}
                  <span className="font-medium text-sm truncate">{s.market_title || s.market_id}</span>
                </div>

                {/* Right meta */}
                <div className="flex items-center gap-2 shrink-0 ml-3">
                  <StrategyBadges strategy={s.strategy} />
                  <span className="text-xs px-1.5 py-0.5 rounded bg-accent-blue/15 text-accent-blue">
                    {s.agent_id || "core_engine"}
                  </span>
                  <span className="text-xs text-muted-dark">{s.exchange}</span>
                </div>
              </div>

              {/* Conviction bar */}
              <div className="mb-3">
                <div className="flex items-center justify-between text-xs text-muted mb-1">
                  <span>Conviction</span>
                  {s.target_price != null && (
                    <span>Target: <span className="font-mono text-foreground">{(s.target_price * 100).toFixed(1)}¢</span></span>
                  )}
                </div>
                <ConvictionBar confidence={s.confidence} />
              </div>

              {/* Reasoning */}
              {s.reasoning && (
                <p className="text-xs text-muted leading-relaxed border-t border-card-border pt-2 mt-2">
                  {s.reasoning}
                </p>
              )}

              {/* Timestamp */}
              <p className="text-xs text-muted-dark mt-2">
                {new Date(s.timestamp).toLocaleString()}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <div className="bg-card border border-card-border rounded-xl p-8 text-center text-muted text-sm">
          No signals yet. Start the bot and add markets to your watchlist.
        </div>
      )}
    </div>
  );
}
