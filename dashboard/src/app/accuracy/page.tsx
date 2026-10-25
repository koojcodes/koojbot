"use client";

import { useEffect, useState } from "react";
import {
  api,
  type ArchetypeAccuracy,
  type ArchetypeWeight,
  type PredictionOutcome,
  type DailySimEntry,
  type DailySimStats,
} from "@/lib/api";

export default function AccuracyPage() {
  const [archetypes, setArchetypes] = useState<ArchetypeAccuracy[]>([]);
  const [weights, setWeights] = useState<Record<string, ArchetypeWeight>>({});
  const [predictions, setPredictions] = useState<PredictionOutcome[]>([]);
  const [dailySims, setDailySims] = useState<DailySimEntry[]>([]);
  const [dailyStats, setDailyStats] = useState<DailySimStats | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustResult, setAdjustResult] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [accuracy, currentWeights, history, daily] = await Promise.all([
          api.getArchetypeAccuracy(),
          api.getArchetypeWeights(),
          api.getPredictionHistory(20),
          api.getDailySimulations(),
        ]);
        setArchetypes(accuracy);
        setWeights(currentWeights);
        setPredictions(history);
        setDailySims(daily.today_results || []);
        setDailyStats(daily.stats || null);
        setError("");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unable to load learning loop data.");
      }
    }

    load();
  }, []);

  const adjustWeights = async () => {
    setAdjusting(true);
    try {
      const data = await api.adjustWeights();
      setAdjustResult(
        data.count > 0
          ? `Applied ${data.count} adjustment(s)`
          : "No adjustments needed"
      );
      setWeights(await api.getArchetypeWeights());
    } catch (e) {
      setAdjustResult(`Error: ${e instanceof Error ? e.message : "adjustment failed"}`);
    }
    setAdjusting(false);
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Learning Loop</h1>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {error}
        </div>
      )}

      {/* Archetype Accuracy */}
      <div className="bg-card rounded-xl border border-card-border p-5 mb-6">
        <h2 className="text-sm font-semibold text-muted mb-4">
          Archetype Prediction Accuracy
        </h2>
        {archetypes.length === 0 ? (
          <p className="text-sm text-muted">
            No prediction data yet. Run simulations and wait for markets to
            resolve.
          </p>
        ) : (
          <div className="space-y-3">
            {archetypes.map((a) => (
              <div
                key={a.archetype}
                className="flex items-center gap-4 p-3 rounded-lg bg-background border border-card-border"
              >
                <div className="flex-1">
                  <p className="text-sm font-medium">{a.archetype}</p>
                  <p className="text-xs text-muted">
                    {a.correct_predictions}/{a.total_predictions} correct | avg
                    confidence: {(a.avg_confidence * 100).toFixed(0)}%
                  </p>
                </div>
                <div className="text-right">
                  <p
                    className={`text-lg font-bold font-mono ${
                      a.accuracy >= 0.55
                        ? "text-accent"
                        : a.accuracy >= 0.45
                        ? "text-accent-amber"
                        : "text-accent-red"
                    }`}
                  >
                    {(a.accuracy * 100).toFixed(0)}%
                  </p>
                </div>
                <div className="w-24 h-2 bg-surface rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      a.accuracy >= 0.55
                        ? "bg-accent"
                        : a.accuracy >= 0.45
                        ? "bg-accent-amber"
                        : "bg-accent-red"
                    }`}
                    style={{ width: `${a.accuracy * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Archetype Weights */}
      <div className="bg-card rounded-xl border border-card-border p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-muted">
            Archetype Influence Weights
          </h2>
          <button
            onClick={adjustWeights}
            disabled={adjusting}
            className="px-3 py-1.5 rounded-lg bg-accent text-black text-xs font-medium hover:opacity-90 disabled:opacity-50"
          >
            {adjusting ? "Adjusting..." : "Auto-Adjust"}
          </button>
        </div>
        {adjustResult && (
          <p className="text-xs text-muted mb-3">{adjustResult}</p>
        )}
        {Object.keys(weights).length > 0 ? (
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(weights)
              .sort(([, a], [, b]) => b.current_weight - a.current_weight)
              .map(([arch, w]) => {
                const delta = w.current_weight - w.baseline_weight;
                return (
                  <div
                    key={arch}
                    className="flex items-center justify-between p-2.5 rounded-lg bg-background border border-card-border"
                  >
                    <div>
                      <p className="text-sm font-medium">{w.name}</p>
                      <p className="text-xs text-muted">
                        {w.total_predictions > 0
                          ? `${(w.accuracy * 100).toFixed(0)}% acc (${w.total_predictions} pred)`
                          : "No data"}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-sm font-mono font-bold">
                        {w.current_weight.toFixed(2)}
                      </p>
                      {Math.abs(delta) > 0.001 && (
                        <p
                          className={`text-xs font-mono ${
                            delta > 0 ? "text-accent" : "text-accent-red"
                          }`}
                        >
                          {delta > 0 ? "+" : ""}
                          {delta.toFixed(3)}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>
        ) : (
          <p className="text-sm text-muted">Loading weights...</p>
        )}
      </div>

      {/* Daily Simulations */}
      <div className="bg-card rounded-xl border border-card-border p-5 mb-6">
        <h2 className="text-sm font-semibold text-muted mb-3">
          Scheduled Daily Simulations
        </h2>
        <div className="grid grid-cols-4 gap-3 mb-4">
          <div className="text-center p-3 bg-background rounded-lg">
            <p className="text-2xl font-bold font-mono">
              {dailyStats?.total_runs ?? 0}
            </p>
            <p className="text-xs text-muted">Total Runs</p>
          </div>
          <div className="text-center p-3 bg-background rounded-lg">
            <p className="text-2xl font-bold font-mono">
              {dailyStats?.sim_hour_utc ?? 8}:00
            </p>
            <p className="text-xs text-muted">Schedule (UTC)</p>
          </div>
          <div className="text-center p-3 bg-background rounded-lg">
            <p className="text-2xl font-bold font-mono">
              {dailyStats?.max_sims_per_day ?? 3}
            </p>
            <p className="text-xs text-muted">Max/Day</p>
          </div>
          <div className="text-center p-3 bg-background rounded-lg">
            <p className="text-sm font-mono">
              {dailyStats?.last_run_date || "Never"}
            </p>
            <p className="text-xs text-muted">Last Run</p>
          </div>
        </div>
        {dailySims.length > 0 && (
          <div className="space-y-2">
            {dailySims.map((sim, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-3 rounded-lg bg-background border border-card-border"
              >
                <div>
                  <p className="text-sm">{sim.market_title}</p>
                  <p className="text-xs text-muted">{sim.status}</p>
                </div>
                <span
                  className={`text-xs px-2 py-1 rounded font-medium ${
                    sim.consensus === "bullish"
                      ? "bg-accent/15 text-accent"
                      : sim.consensus === "bearish"
                      ? "bg-accent-red/15 text-accent-red"
                      : "bg-accent-amber/15 text-accent-amber"
                  }`}
                >
                  {sim.consensus}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent Predictions */}
      <div className="bg-card rounded-xl border border-card-border p-5">
        <h2 className="text-sm font-semibold text-muted mb-3">
          Recent Prediction Outcomes
        </h2>
        {predictions.length === 0 ? (
          <p className="text-sm text-muted">No resolved predictions yet.</p>
        ) : (
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {predictions.map((p, i) => (
              <div
                key={i}
                className="flex items-center gap-3 p-3 rounded-lg bg-background border border-card-border"
              >
                <span className="text-lg">
                  {p.was_correct ? "✅" : "❌"}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">
                    {p.market_title || p.topic}
                  </p>
                  <p className="text-xs text-muted">
                    {p.archetype} predicted {p.predicted_direction} | actual:{" "}
                    {p.actual_outcome}
                  </p>
                </div>
                <p className="text-xs text-muted whitespace-nowrap">
                  {p.resolved_at?.split("T")[0]}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
