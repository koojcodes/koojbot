"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function BotStatusBar() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [paper, setPaper] = useState<boolean>(true);
  const [balance, setBalance] = useState<number | null>(null);

  useEffect(() => {
    async function check() {
      try {
        const [health, bal] = await Promise.all([
          api.getHealth(),
          api.getBalance(),
        ]);
        setOnline(true);
        setPaper(health.paper_mode);
        setBalance(bal.balance);
      } catch {
        setOnline(false);
        setBalance(null);
      }
    }
    check();
    const interval = setInterval(check, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-2">
      {/* Status row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs">
          <span
            className={`inline-block w-2 h-2 rounded-full ${
              online === null
                ? "bg-muted-dark animate-pulse"
                : online
                ? "bg-accent shadow-[0_0_6px_rgba(34,197,94,0.6)]"
                : "bg-accent-red"
            }`}
          />
          <span className={online ? "text-foreground" : "text-muted"}>
            {online === null ? "Connecting..." : online ? "Bot Running" : "Offline"}
          </span>
        </div>
        {paper && (
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-accent-amber/20 text-accent-amber tracking-wide">
            PAPER
          </span>
        )}
      </div>

      {/* Balance */}
      {balance !== null && (
        <div className="text-xs text-muted font-mono">
          Balance:{" "}
          <span className="text-foreground font-medium">
            {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(balance)}
          </span>
        </div>
      )}

      {/* Strategy tags */}
      <div className="flex gap-1 flex-wrap">
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-blue/15 text-accent-blue">
          News
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-amber/15 text-accent-amber">
          Momentum
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent">
          Conviction
        </span>
      </div>
    </div>
  );
}
