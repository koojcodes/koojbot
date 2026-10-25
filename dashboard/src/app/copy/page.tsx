"use client";

import { useEffect, useState } from "react";
import { api, CopyLeaderStats, CopyStats, CopyTradeData } from "@/lib/api";

function shortenWallet(wallet: string) {
  if (wallet.length <= 12) return wallet;
  return `${wallet.slice(0, 6)}…${wallet.slice(-4)}`;
}

export default function CopyTradingPage() {
  const [stats, setStats] = useState<CopyStats | null>(null);
  const [leaders, setLeaders] = useState<CopyLeaderStats[]>([]);
  const [trades, setTrades] = useState<CopyTradeData[]>([]);
  const [discover, setDiscover] = useState<
    { wallet: string; name: string; recent_trades: number }[]
  >([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const [s, l, t] = await Promise.all([
          api.getCopyStats(),
          api.getCopyLeaders(),
          api.getCopyTrades(50),
        ]);
        setStats(s);
        setLeaders(l);
        setTrades(t);
        setError("");
      } catch (e) {
        setError(e instanceof Error ? e.message : "API unavailable");
      }
    };
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  const loadDiscover = async () => {
    try {
      const rows = await api.discoverCopyLeaders(8);
      setDiscover(rows);
    } catch {
      setDiscover([]);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100">Copy Trading</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Mirror Polymarket leader wallets configured in POLY_COPY_LEADERS
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error} — start the bot with <code className="text-red-200">--api</code>
        </div>
      )}

      {stats && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Leaders configured" value={String(stats.leaders_configured)} />
          <Metric label="Leader trades seen" value={String(stats.leader_trades_seen)} />
          <Metric label="Mirrored" value={String(stats.leader_trades_mirrored)} />
          <Metric label="Copy executions" value={String(stats.copy_executions)} />
        </div>
      )}

      {stats && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 text-sm text-zinc-400">
          <span className="text-zinc-300">Sizing:</span> {stats.sizing_mode}
          {stats.sizing_mode === "fixed_usd" && ` ($${stats.fixed_usd})`}
          <span className="mx-2">·</span>
          Poll every {stats.poll_interval_seconds}s
          <span className="mx-2">·</span>
          Max lag {stats.max_lag_seconds}s
        </div>
      )}

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-medium text-zinc-200">Leaders</h2>
          <button
            type="button"
            onClick={loadDiscover}
            className="rounded-md bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700"
          >
            Discover active wallets
          </button>
        </div>
        {leaders.length === 0 ? (
          <p className="text-sm text-zinc-500">No leader activity yet. Add wallets to .env</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-zinc-500">
              <tr>
                <th className="pb-2">Wallet</th>
                <th className="pb-2">Seen</th>
                <th className="pb-2">Mirrored</th>
                <th className="pb-2">Last activity</th>
              </tr>
            </thead>
            <tbody>
              {leaders.map((l) => (
                <tr key={l.leader_wallet} className="border-t border-zinc-800 text-zinc-300">
                  <td className="py-2 font-mono text-xs">{shortenWallet(l.leader_wallet)}</td>
                  <td className="py-2">{l.trades_seen}</td>
                  <td className="py-2">{l.trades_mirrored}</td>
                  <td className="py-2 text-zinc-500">{l.last_seen_at || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {discover.length > 0 && (
          <div className="mt-4 border-t border-zinc-800 pt-4">
            <p className="mb-2 text-xs text-zinc-500">Recent active wallets (add to POLY_COPY_LEADERS)</p>
            <ul className="space-y-1 text-xs font-mono text-zinc-400">
              {discover.map((d) => (
                <li key={d.wallet}>
                  {d.wallet} — {d.recent_trades} recent trades
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {stats?.skip_reasons && stats.skip_reasons.length > 0 && (
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <h2 className="mb-3 font-medium text-zinc-200">Skip reasons</h2>
          <ul className="space-y-1 text-sm text-zinc-400">
            {stats.skip_reasons.map((s) => (
              <li key={s.skip_reason}>
                {s.skip_reason} <span className="text-zinc-600">({s.count})</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <h2 className="mb-3 font-medium text-zinc-200">Copy executions</h2>
        {trades.length === 0 ? (
          <p className="text-sm text-zinc-500">No copy trades yet</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-zinc-500">
              <tr>
                <th className="pb-2">Time</th>
                <th className="pb-2">Leader</th>
                <th className="pb-2">Side</th>
                <th className="pb-2">Price</th>
                <th className="pb-2">Size</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} className="border-t border-zinc-800 text-zinc-300">
                  <td className="py-2 text-xs text-zinc-500">{t.created_at}</td>
                  <td className="py-2 font-mono text-xs">
                    {t.leader_wallet ? shortenWallet(t.leader_wallet) : "—"}
                  </td>
                  <td className="py-2">{t.side}</td>
                  <td className="py-2">{t.price.toFixed(3)}</td>
                  <td className="py-2">{t.size.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-zinc-100">{value}</p>
    </div>
  );
}
