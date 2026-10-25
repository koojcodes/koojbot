"use client";

import { useEffect, useState } from "react";
import { api, type SimulationEntry, type SimulationResult } from "@/lib/api";

export default function SimulationsPage() {
  const [simulations, setSimulations] = useState<SimulationEntry[]>([]);
  const [newTopic, setNewTopic] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");

  async function loadSimulations() {
    try {
      const data = await api.getSimulations();
      setSimulations(data);
      setHistoryError("");
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "Unable to load simulations.");
    }
  }

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadSimulations();
    }, 0);
    return () => clearTimeout(timer);
  }, []);

  const runSimulation = async () => {
    if (!newTopic.trim() || running) return;
    setRunning(true);
    setResult(null);
    setError("");

    try {
      const data = await api.runSimulation(newTopic, 3);
      setResult(data);
      await loadSimulations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Simulation failed.");
    }
    setRunning(false);
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Swarm Simulations</h1>

      {/* New simulation */}
      <div className="bg-card rounded-xl border border-card-border p-5 mb-6">
        <h2 className="text-sm font-semibold text-muted mb-3">Run Simulation</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={newTopic}
            onChange={(e) => setNewTopic(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSimulation()}
            placeholder="Market scenario (e.g. BTC breaks 100k after ETF inflows)"
            className="flex-1 px-3 py-2 rounded-lg bg-background border border-card-border text-sm focus:outline-none focus:border-accent"
          />
          <button
            onClick={runSimulation}
            disabled={running}
            className="px-4 py-2 rounded-lg bg-accent text-black text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {running ? "Running..." : "Simulate"}
          </button>
        </div>

        {running && (
          <p className="text-sm text-muted mt-3">
            Simulation in progress — 10 trader archetypes debating the scenario...
          </p>
        )}

        {error && (
          <p className="text-sm text-accent-red mt-3">{error}</p>
        )}
      </div>

      {historyError && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 mb-6 text-sm">
          {historyError}
        </div>
      )}

      {/* Simulation result */}
      {result && (
        <div className="bg-card rounded-xl border border-card-border p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold">
              {result.topic}
            </h2>
            <span
              className={`text-xs px-2 py-1 rounded ${
                result.status === "completed"
                  ? "bg-accent/15 text-accent"
                  : "bg-accent-red/15 text-accent-red"
              }`}
            >
              {result.status}
            </span>
          </div>

          <div className="grid grid-cols-3 gap-3 mb-4">
            <div className="text-center p-3 bg-background rounded-lg">
              <p className="text-2xl font-bold font-mono">{result.total_actions}</p>
              <p className="text-xs text-muted">Actions</p>
            </div>
            <div className="text-center p-3 bg-background rounded-lg">
              <p className="text-2xl font-bold font-mono">{result.num_rounds}</p>
              <p className="text-xs text-muted">Rounds</p>
            </div>
            <div className="text-center p-3 bg-background rounded-lg">
              <p className="text-2xl font-bold font-mono">
                {Object.keys(result.by_agent).length}
              </p>
              <p className="text-xs text-muted">Active Agents</p>
            </div>
          </div>

          {/* Agent activity */}
          {Object.keys(result.by_agent).length > 0 && (
            <div className="mb-4">
              <h3 className="text-xs font-semibold text-muted mb-2">Agent Activity</h3>
              <div className="space-y-1">
                {Object.entries(result.by_agent)
                  .sort(([, a], [, b]) => b - a)
                  .map(([name, count]) => (
                    <div key={name} className="flex justify-between text-sm">
                      <span>{name}</span>
                      <span className="font-mono text-muted">{count}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* Posts */}
          {result.posts.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-muted mb-2">
                Posts ({result.posts.length})
              </h3>
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {result.posts.map((post, i) => (
                  <div
                    key={i}
                    className="text-sm p-3 rounded-lg bg-background border border-card-border"
                  >
                    {post}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Past simulations */}
      {simulations.length > 0 && (
        <div className="bg-card rounded-xl border border-card-border p-5">
          <h2 className="text-sm font-semibold text-muted mb-3">Past Simulations</h2>
          <div className="space-y-2">
            {simulations
              .slice()
              .reverse()
              .map((sim) => (
                <div
                  key={sim.simulation_id}
                  className="flex items-center justify-between p-3 rounded-lg bg-background border border-card-border"
                >
                  <div>
                    <p className="text-sm font-mono">{sim.simulation_id}</p>
                    <p className="text-xs text-muted">
                      {sim.total_actions} actions, {sim.current_round} rounds
                    </p>
                  </div>
                  <span
                    className={`text-xs px-2 py-1 rounded ${
                      sim.status === "completed"
                        ? "bg-accent/15 text-accent"
                        : sim.status === "running"
                        ? "bg-accent-amber/15 text-accent-amber"
                        : "bg-accent-red/15 text-accent-red"
                    }`}
                  >
                    {sim.status}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
