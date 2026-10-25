"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type MarketData } from "@/lib/api";

interface WatchlistEntry {
  id: string;
  title: string;
  exchange: string;
  prices: Record<string, number>;
  volume: number;
  auto_discovered: boolean;
  price_history: { time: string; price: number }[];
}

export default function MarketsPage() {
  const [markets, setMarkets] = useState<MarketData[]>([]);
  const [watchlist, setWatchlist] = useState<Record<string, WatchlistEntry>>({});
  const [watchedIds, setWatchedIds] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [watchlistError, setWatchlistError] = useState<string | null>(null);
  const [toggleLoading, setToggleLoading] = useState<string | null>(null);

  const loadWatchlist = useCallback(async () => {
    try {
      const wl = (await api.getWatchlist()) as Record<string, WatchlistEntry>;
      setWatchlist(wl);
      setWatchedIds(new Set(Object.keys(wl)));
      setWatchlistError(null);
    } catch {
      setWatchlistError("Watchlist data is unavailable right now.");
    }
  }, []);

  async function search(q: string) {
    setSearching(true);
    try {
      const results = await api.getMarkets(q, 30);
      setMarkets(results);
      setError(null);
    } catch {
      setError("Cannot connect to bot API.");
    }
    setSearching(false);
  }

  async function toggleWatch(marketId: string, isWatched: boolean) {
    setToggleLoading(marketId);
    try {
      if (isWatched) {
        await api.removeFromWatchlist(marketId);
        setWatchedIds((prev) => { const next = new Set(prev); next.delete(marketId); return next; });
      } else {
        await api.addToWatchlist(marketId);
        setWatchedIds((prev) => new Set(prev).add(marketId));
      }
      await loadWatchlist();
      setWatchlistError(null);
    } catch {
      setWatchlistError(
        isWatched
          ? "Unable to remove this market from the watchlist."
          : "Unable to add this market to the watchlist."
      );
    }
    setToggleLoading(null);
  }

  useEffect(() => {
    const timer = setTimeout(() => {
      void search("");
      void loadWatchlist();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadWatchlist]);

  // Refresh watchlist every 15s
  useEffect(() => {
    const interval = setInterval(loadWatchlist, 15000);
    return () => clearInterval(interval);
  }, [loadWatchlist]);

  const watchlistEntries = Object.values(watchlist);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Markets</h2>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {error}
        </div>
      )}

      {watchlistError && (
        <div className="bg-accent-amber/10 border border-accent-amber/30 rounded-xl p-4 mb-6 text-sm">
          {watchlistError}
        </div>
      )}

      {/* Active Watchlist */}
      {watchlistEntries.length > 0 && (
        <div className="mb-8">
          <h3 className="text-xs text-muted uppercase tracking-widest font-medium mb-3">
            Watching ({watchlistEntries.length})
          </h3>
          <div className="grid gap-2">
            {watchlistEntries.map((w) => (
              <div key={w.id} className="bg-card border border-accent/20 rounded-xl p-3 flex items-center justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm truncate">{w.title}</span>
                    {w.auto_discovered && (
                      <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-accent-blue/15 text-accent-blue font-medium">
                        🤖 Auto
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5 text-xs text-muted">
                    <span>{w.exchange}</span>
                    <span>Vol: {w.volume.toLocaleString()}</span>
                    <span>{w.price_history.length} price pts</span>
                  </div>
                </div>
                <div className="flex items-center gap-4 shrink-0">
                  {Object.entries(w.prices).map(([outcome, price]) => (
                    <div key={outcome} className="text-center">
                      <p className="text-xs text-muted">{outcome}</p>
                      <p className="font-mono font-bold text-sm">{(price * 100).toFixed(1)}¢</p>
                    </div>
                  ))}
                  <button
                    onClick={() => toggleWatch(w.id, true)}
                    disabled={toggleLoading === w.id}
                    className="text-xs px-2.5 py-1.5 rounded-lg bg-accent-red/10 text-accent-red border border-accent-red/20 hover:bg-accent-red/20 transition-colors disabled:opacity-50"
                  >
                    Unwatch
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Search */}
      <div className="mb-5">
        <div className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search(query)}
            placeholder="Search markets (e.g. 'bitcoin', 'fed rate', 'election')..."
            className="flex-1 bg-card border border-card-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-accent transition-colors"
          />
          <button
            onClick={() => search(query)}
            disabled={searching}
            className="bg-accent text-black px-5 py-2.5 rounded-lg text-sm font-bold hover:bg-accent/90 disabled:opacity-50 transition-colors"
          >
            {searching ? "..." : "Search"}
          </button>
        </div>
      </div>

      {/* Search results */}
      {markets.length > 0 ? (
        <div>
          <h3 className="text-xs text-muted uppercase tracking-widest font-medium mb-3">
            Results ({markets.length})
          </h3>
          <div className="grid gap-2">
            {markets.map((m, i) => {
              const isWatched = watchedIds.has(m.id);
              return (
                <div key={i} className="bg-card border border-card-border rounded-xl p-3 flex items-center justify-between gap-4 hover:border-muted-dark transition-colors">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-medium text-sm truncate">{m.title}</h3>
                    <div className="flex items-center gap-3 mt-0.5 text-xs text-muted">
                      <span>{m.exchange}</span>
                      {m.category && <span className="text-muted-dark">{m.category}</span>}
                      <span>Vol: {m.volume.toLocaleString()}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    {Object.entries(m.prices).map(([outcome, price]) => (
                      <div key={outcome} className="text-center min-w-[40px]">
                        <p className="text-xs text-muted">{outcome}</p>
                        <p className="font-mono font-bold text-sm">{(price * 100).toFixed(1)}¢</p>
                      </div>
                    ))}
                    <button
                      onClick={() => toggleWatch(m.id, isWatched)}
                      disabled={toggleLoading === m.id}
                      className={`text-xs px-2.5 py-1.5 rounded-lg border transition-colors disabled:opacity-50 ${
                        isWatched
                          ? "bg-accent-red/10 text-accent-red border-accent-red/20 hover:bg-accent-red/20"
                          : "bg-accent/10 text-accent border-accent/20 hover:bg-accent/20"
                      }`}
                    >
                      {toggleLoading === m.id ? "..." : isWatched ? "Unwatch" : "+ Watch"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="bg-card border border-card-border rounded-xl p-8 text-center text-muted text-sm">
          {searching ? "Searching..." : "No markets found. Try a different search."}
        </div>
      )}
    </div>
  );
}
