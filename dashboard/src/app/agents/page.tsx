"use client";

import { useEffect, useState } from "react";
import {
  api,
  type AgentQualityGate,
  type AgentRegistryEntry,
  type OrchestrationStatus,
} from "@/lib/api";

function formatDate(value: string | null | undefined): string {
  if (!value) return "--";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleString();
}

export default function AgentsPage() {
  const [status, setStatus] = useState<OrchestrationStatus | null>(null);
  const [registry, setRegistry] = useState<AgentRegistryEntry[]>([]);
  const [qualityGates, setQualityGates] = useState<Record<string, AgentQualityGate>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [orchestration, registryData, gateData] = await Promise.all([
          api.getOrchestrationStatus(),
          api.getAgentRegistry(),
          api.getAgentQualityGates(),
        ]);
        setStatus(orchestration);
        setRegistry(registryData);
        setQualityGates(gateData);
        setError(null);
      } catch {
        setError("Cannot connect to orchestration status endpoint.");
      }
    }

    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const orchestrator = status?.orchestrator;
  const learning = status?.learning_pipeline;
  const traders = status?.traders ?? [];
  const runtimeAgents = registry.length;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold">Claude Agent Orchestrator</h2>
          <p className="text-sm text-muted mt-1">
            Live view of orchestration loop, centralized learning pipeline, and trader runtime activity.
          </p>
        </div>
        <span
          className={`text-xs px-2.5 py-1 rounded border ${
            orchestrator?.engine_running
              ? "border-accent/40 bg-accent/10 text-accent"
              : "border-accent-red/40 bg-accent-red/10 text-accent-red"
          }`}
        >
          {orchestrator?.engine_running ? "Engine Running" : "Engine Offline"}
        </span>
      </div>

      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-xl p-4 text-sm">
          {error}
        </div>
      )}

      <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">Cycle Count</p>
          <p className="text-2xl font-mono font-bold mt-1">{orchestrator?.cycle_count ?? "--"}</p>
        </div>
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">Poll Interval</p>
          <p className="text-2xl font-mono font-bold mt-1">
            {orchestrator?.poll_interval_seconds != null
              ? `${orchestrator.poll_interval_seconds}s`
              : "--"}
          </p>
        </div>
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">Runtime Agents</p>
          <p className="text-2xl font-mono font-bold mt-1">
            {orchestrator?.active_runtime_agents ?? "--"}
          </p>
        </div>
        <div className="bg-card border border-card-border rounded-xl p-4">
          <p className="text-xs text-muted uppercase">Recent Signals (buffer)</p>
          <p className="text-2xl font-mono font-bold mt-1">
            {orchestrator?.recent_signal_count ?? "--"}
          </p>
        </div>
      </div>

      <section className="bg-card border border-card-border rounded-xl p-4">
        <h3 className="text-sm text-muted uppercase tracking-wide mb-3">Learning Pipeline</h3>
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="rounded-lg border border-card-border bg-surface p-3">
            <p className="text-xs text-muted uppercase">Builder Agent</p>
            <p className="text-sm font-medium mt-1">{learning?.builder.agent_id ?? "knowledge_builder"}</p>
          </div>
          <div className="rounded-lg border border-card-border bg-surface p-3">
            <p className="text-xs text-muted uppercase">Builder Cadence</p>
            <p className="text-sm font-medium mt-1">
              {learning?.builder.cadence_seconds != null ? `${learning.builder.cadence_seconds}s` : "--"}
            </p>
          </div>
          <div className="rounded-lg border border-card-border bg-surface p-3">
            <p className="text-xs text-muted uppercase">Validator Agent</p>
            <p className="text-sm font-medium mt-1">
              {learning?.validator.agent_id ?? "knowledge_validator"}
            </p>
          </div>
          <div className="rounded-lg border border-card-border bg-surface p-3">
            <p className="text-xs text-muted uppercase">Validator Cadence</p>
            <p className="text-sm font-medium mt-1">
              {learning?.validator.cadence_seconds != null
                ? `${learning.validator.cadence_seconds}s`
                : "--"}
            </p>
          </div>
        </div>
        <div className="mt-3 text-xs text-muted">
          Registry: <span className="font-mono text-foreground">{learning?.registry_version ?? "--"}</span>{" "}
          · Contracts:{" "}
          <span className="font-mono text-foreground">{learning?.contracts_file ?? "--"}</span>
        </div>
      </section>

      <section className="bg-card border border-card-border rounded-xl p-4">
        <h3 className="text-sm text-muted uppercase tracking-wide mb-3">
          Runtime Registry ({runtimeAgents})
        </h3>
        {registry.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left border-b border-card-border">
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 font-medium">Runner</th>
                  <th className="pb-2 font-medium">Exchange</th>
                  <th className="pb-2 font-medium">Timeframe</th>
                  <th className="pb-2 font-medium">Quality Gate</th>
                </tr>
              </thead>
              <tbody>
                {registry.map((entry) => {
                  const gate = qualityGates[entry.agent_id];
                  return (
                    <tr key={entry.agent_id} className="border-b border-card-border/50 align-top">
                      <td className="py-2">
                        <div className="font-medium">{entry.agent_id}</div>
                        <div className="text-xs text-muted">{entry.notes || "No notes"}</div>
                      </td>
                      <td className="py-2">{entry.runner}</td>
                      <td className="py-2">{entry.exchange}</td>
                      <td className="py-2">{entry.timeframe}</td>
                      <td className="py-2 text-xs text-muted">
                        {gate
                          ? `conf ${Math.round(gate.min_signal_confidence * 100)}% · sample ${gate.min_sample_size}`
                          : "Not defined"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-muted text-sm">Runtime registry is unavailable.</p>
        )}
      </section>

      <section className="bg-card border border-card-border rounded-xl p-4">
        <h3 className="text-sm text-muted uppercase tracking-wide mb-3">
          Trader Agent Activity ({traders.length})
        </h3>
        {traders.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left border-b border-card-border">
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 font-medium">Enabled</th>
                  <th className="pb-2 font-medium">Cadence</th>
                  <th className="pb-2 font-medium text-right">Signals (1h / total)</th>
                  <th className="pb-2 font-medium text-right">Trades (24h / total)</th>
                  <th className="pb-2 font-medium">Last Signal</th>
                  <th className="pb-2 font-medium">Last Trade</th>
                </tr>
              </thead>
              <tbody>
                {traders.map((trader) => (
                  <tr key={trader.agent_id} className="border-b border-card-border/50 align-top">
                    <td className="py-2">
                      <div className="font-medium">{trader.agent_id}</div>
                      <div className="text-xs text-muted">
                        {[trader.runner, trader.exchange, trader.timeframe].filter(Boolean).join(" · ") || "n/a"}
                      </div>
                    </td>
                    <td className="py-2">
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          trader.enabled
                            ? "bg-accent/15 text-accent"
                            : "bg-accent-red/15 text-accent-red"
                        }`}
                      >
                        {trader.enabled ? "yes" : "no"}
                      </span>
                    </td>
                    <td className="py-2 font-mono">
                      {trader.cadence_seconds != null ? `${trader.cadence_seconds}s` : "--"}
                    </td>
                    <td className="py-2 text-right font-mono">
                      {trader.signals_last_1h} / {trader.signals_total}
                    </td>
                    <td className="py-2 text-right font-mono">
                      {trader.trades_last_24h} / {trader.trades_total}
                    </td>
                    <td className="py-2">
                      <div className="text-xs text-muted">{formatDate(trader.last_signal_at)}</div>
                      {trader.last_signal_reasoning && (
                        <div className="text-xs text-muted mt-1 max-w-[360px] truncate">
                          {trader.last_signal_reasoning}
                        </div>
                      )}
                    </td>
                    <td className="py-2 text-xs text-muted">{formatDate(trader.last_trade_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-muted text-sm">No trader agents detected yet.</p>
        )}
      </section>
    </div>
  );
}
