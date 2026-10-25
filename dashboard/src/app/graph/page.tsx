"use client";

import { useEffect, useState } from "react";
import { api, type GraphSearchResult, type GraphStats } from "@/lib/api";

export default function GraphPage() {
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<GraphSearchResult | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    api.getGraphStats()
      .then((data) => {
        setStats(data);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const data = await api.searchGraph(searchQuery, 10);
      setSearchResults(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Graph search failed.");
    }
    setSearching(false);
  };

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-bold mb-4">Knowledge Graph</h1>
        <div className="bg-card rounded-xl border border-card-border p-6">
          <p className="text-muted">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Knowledge Graph</h1>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <StatCard label="Nodes" value={stats.total_nodes} />
          <StatCard label="Edges" value={stats.total_edges} />
          <StatCard label="Entity Types" value={Object.keys(stats.entity_types).length} />
          <StatCard label="Expired Edges" value={stats.expired_edges} />
        </div>
      )}

      {/* Entity types breakdown */}
      {stats && Object.keys(stats.entity_types).length > 0 && (
        <div className="grid grid-cols-2 gap-4 mb-6">
          <div className="bg-card rounded-xl border border-card-border p-5">
            <h2 className="text-sm font-semibold text-muted mb-3">Entity Types</h2>
            <div className="space-y-2">
              {Object.entries(stats.entity_types)
                .sort(([, a], [, b]) => b - a)
                .map(([type, count]) => (
                  <div key={type} className="flex justify-between text-sm">
                    <span>{type}</span>
                    <span className="font-mono text-muted">{count}</span>
                  </div>
                ))}
            </div>
          </div>
          <div className="bg-card rounded-xl border border-card-border p-5">
            <h2 className="text-sm font-semibold text-muted mb-3">Relationship Types</h2>
            <div className="space-y-2">
              {Object.entries(stats.relation_types)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 10)
                .map(([type, count]) => (
                  <div key={type} className="flex justify-between text-sm">
                    <span>{type}</span>
                    <span className="font-mono text-muted">{count}</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}

      {/* Search */}
      <div className="bg-card rounded-xl border border-card-border p-5 mb-6">
        <h2 className="text-sm font-semibold text-muted mb-3">Graph Search</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Search entities and facts (e.g. RSI BTC momentum)"
            className="flex-1 px-3 py-2 rounded-lg bg-background border border-card-border text-sm focus:outline-none focus:border-accent"
          />
          <button
            onClick={handleSearch}
            disabled={searching}
            className="px-4 py-2 rounded-lg bg-accent text-black text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {searching ? "..." : "Search"}
          </button>
        </div>

        {searchResults && (
          <div className="mt-4 space-y-2">
            <p className="text-xs text-muted">
              {searchResults.total_count} results for &quot;{searchResults.query}&quot;
            </p>
            {searchResults.facts.map((fact, i) => (
              <div
                key={i}
                className="text-sm p-3 rounded-lg bg-background border border-card-border"
              >
                {fact}
              </div>
            ))}
          </div>
        )}
      </div>

      {!stats && !error && (
        <div className="bg-card rounded-xl border border-card-border p-6">
          <p className="text-muted">Loading graph statistics...</p>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-card rounded-xl border border-card-border p-4">
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className="text-2xl font-bold font-mono">{value.toLocaleString()}</p>
    </div>
  );
}
