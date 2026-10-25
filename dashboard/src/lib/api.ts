const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchAPI<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const data = await res.json();
  if (data && typeof data === "object" && "error" in data && data.error) {
    throw new Error(String(data.error));
  }
  return data;
}

export interface PortfolioState {
  balance: number;
  total_value: number;
  total_exposure: number;
  unrealized_pnl: number;
  realized_pnl: number;
  num_positions: number;
  timestamp: string;
  positions: PositionData[];
}

export interface PositionData {
  market_id: string;
  market_title: string;
  outcome: string;
  size: number;
  avg_entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  exchange: string;
}

export interface TradeData {
  id: string;
  market_id: string;
  exchange: string;
  side: string;
  outcome: string;
  price: number;
  size: number;
  pnl: number;
  strategy: string;
  agent_id: string;
  paper: number;
  created_at: string;
}

export interface SignalData {
  market_id: string;
  market_title: string;
  exchange: string;
  action: string;
  outcome: string;
  confidence: number;
  target_price: number | null;
  reasoning: string;
  strategy: string;
  agent_id: string;
  execution_exchange?: string | null;
  timestamp: string;
}

export interface AgentRegistryEntry {
  agent_id: string;
  market_type: string;
  exchange: string;
  timeframe: string;
  enabled: boolean;
  runner: string;
  notes: string;
}

export interface AgentQualityGate {
  min_signal_confidence: number;
  max_daily_drawdown_pct: number;
  min_sample_size: number;
  min_win_rate: number;
  min_net_edge_pct: number;
}

export interface OrchestrationTraderStatus {
  agent_id: string;
  enabled: boolean;
  cadence_seconds: number | null;
  runner: string | null;
  exchange: string | null;
  timeframe: string | null;
  market_type: string | null;
  signals_total: number;
  signals_last_1h: number;
  trades_total: number;
  trades_last_24h: number;
  recent_engine_signals: number;
  last_signal_at: string | null;
  last_signal_reasoning: string | null;
  last_trade_at: string | null;
  quality_gate?: AgentQualityGate | null;
}

export interface OrchestrationStatus {
  timestamp: string;
  orchestrator: {
    engine_running: boolean;
    cycle_count: number;
    poll_interval_seconds: number | null;
    snapshot_interval_seconds: number | null;
    active_runtime_agents: number;
    recent_signal_count: number;
  };
  learning_pipeline: {
    builder: {
      agent_id: string;
      cadence_seconds: number | null;
    };
    validator: {
      agent_id: string;
      cadence_seconds: number | null;
    };
    contracts_file: string | null;
    registry_version: string | null;
  };
  traders: OrchestrationTraderStatus[];
}

export interface MarketData {
  id: string;
  title: string;
  exchange: string;
  prices: Record<string, number>;
  volume: number;
  category: string;
}

export interface PortfolioSnapshot {
  balance: number;
  total_exposure: number;
  unrealized_pnl: number;
  realized_pnl: number;
  num_positions: number;
  created_at: string;
}

export interface CopyLeaderStats {
  leader_wallet: string;
  trades_seen: number;
  trades_mirrored: number;
  last_seen_at: string | null;
  configured?: boolean;
}

export interface CopyTradeData {
  id: string;
  market_id: string;
  exchange: string;
  side: string;
  outcome: string;
  price: number;
  size: number;
  leader_wallet: string;
  created_at: string;
}

export interface CopyStats {
  leader_trades_seen: number;
  leader_trades_mirrored: number;
  copy_executions: number;
  leaders_configured: number;
  poll_interval_seconds: number;
  sizing_mode: string;
  fixed_usd: number;
  max_lag_seconds: number;
  agent_enabled: boolean;
  last_poll_at?: string | null;
  skip_reasons?: { skip_reason: string; count: number }[];
}

export interface GraphStats {
  graph_id: string;
  total_nodes: number;
  total_edges: number;
  entity_types: Record<string, number>;
  relation_types: Record<string, number>;
  expired_edges: number;
}

export interface GraphSearchResult {
  facts: string[];
  query: string;
  total_count: number;
}

export interface SimulationEntry {
  simulation_id: string;
  status: string;
  start_time: number | null;
  end_time: number | null;
  total_actions: number;
  current_round: number;
  max_rounds: number;
  error: string;
}

export interface SimulationResult {
  simulation_id: string;
  topic: string;
  status: string;
  total_actions: number;
  num_rounds: number;
  posts: string[];
  by_agent: Record<string, number>;
  by_type: Record<string, number>;
  error: string;
}

export const api = {
  getPortfolio: () => fetchAPI<PortfolioState>("/api/portfolio"),
  getPortfolioHistory: (limit = 100) =>
    fetchAPI<PortfolioSnapshot[]>(`/api/portfolio/history?limit=${limit}`),
  getPositions: () => fetchAPI<PositionData[]>("/api/positions"),
  getTrades: (limit = 100, agentId = "") =>
    fetchAPI<TradeData[]>(
      `/api/trades?limit=${limit}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""}`
    ),
  getSignals: (limit = 50, agentId = "") =>
    fetchAPI<SignalData[]>(
      `/api/signals?limit=${limit}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""}`
    ),
  getMarkets: (query = "", limit = 20) =>
    fetchAPI<MarketData[]>(`/api/markets?query=${query}&limit=${limit}`),
  getWatchlist: () =>
    fetchAPI<Record<string, unknown>>("/api/markets/watchlist"),
  addToWatchlist: async (marketId: string) => {
    const res = await fetch(`${API_BASE}/api/watchlist/${marketId}`, { method: "POST" });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
  },
  removeFromWatchlist: async (marketId: string) => {
    const res = await fetch(`${API_BASE}/api/watchlist/${marketId}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
  },
  getBalance: () => fetchAPI<{ balance: number }>("/api/balance"),
  getHealth: () => fetchAPI<{ status: string; paper_mode: boolean }>("/api/health"),

  // Swarm Intelligence
  getGraphStats: () => fetchAPI<GraphStats>("/api/graph/stats"),
  searchGraph: (q: string, limit = 10) =>
    fetchAPI<GraphSearchResult>(`/api/graph/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  getSimulations: () => fetchAPI<SimulationEntry[]>("/api/swarm/simulations"),
  getSimulationStatus: (simId: string) => fetchAPI<SimulationEntry>(`/api/swarm/status/${simId}`),
  runSimulation: async (topic: string, maxRounds = 3) => {
    const res = await fetch(
      `${API_BASE}/api/swarm/simulate?topic=${encodeURIComponent(topic)}&max_rounds=${maxRounds}`,
      { method: "POST" }
    );
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = (await res.json()) as SimulationResult | { error?: string };
    if ("error" in data && data.error) throw new Error(data.error);
    return data as SimulationResult;
  },

  // Phase 6: Auto-Triggers & Learning Loop
  getArchetypeAccuracy: () =>
    fetchAPI<ArchetypeAccuracy[]>("/api/accuracy/archetypes"),
  getPredictionHistory: (limit = 50) =>
    fetchAPI<PredictionOutcome[]>(`/api/accuracy/predictions?limit=${limit}`),
  getArchetypeWeights: () =>
    fetchAPI<Record<string, ArchetypeWeight>>("/api/weights"),
  getDailySimulations: () =>
    fetchAPI<{ stats: DailySimStats; today_results: DailySimEntry[] }>(
      "/api/daily-simulations"
    ),
  adjustWeights: async () => {
    const res = await fetch(`${API_BASE}/api/weights/adjust`, { method: "POST" });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    const data = (await res.json()) as { count: number; changes: unknown[]; error?: string };
    if (data.error) throw new Error(data.error);
    return data;
  },
  getAgentRegistry: () =>
    fetchAPI<AgentRegistryEntry[]>("/api/agents/registry"),
  getAgentQualityGates: () =>
    fetchAPI<Record<string, AgentQualityGate>>("/api/agents/quality-gates"),
  getOrchestrationStatus: () =>
    fetchAPI<OrchestrationStatus>("/api/orchestration/status"),

  getCopyStats: () => fetchAPI<CopyStats>("/api/copy/stats"),
  getCopyLeaders: () => fetchAPI<CopyLeaderStats[]>("/api/copy/leaders"),
  getCopyTrades: (limit = 100, leaderWallet = "") =>
    fetchAPI<CopyTradeData[]>(
      `/api/copy/trades?limit=${limit}${
        leaderWallet ? `&leader_wallet=${encodeURIComponent(leaderWallet)}` : ""
      }`
    ),
  discoverCopyLeaders: (limit = 10) =>
    fetchAPI<{ wallet: string; name: string; recent_trades: number }[]>(
      `/api/copy/discover?limit=${limit}`
    ),
};

export interface ArchetypeAccuracy {
  archetype: string;
  total_predictions: number;
  correct_predictions: number;
  accuracy: number;
  avg_confidence: number;
}

export interface PredictionOutcome {
  simulation_id: string;
  market_id: string;
  market_title: string;
  predicted_direction: string;
  actual_outcome: string;
  was_correct: number;
  resolved_at: string;
  archetype: string;
  agent_name: string;
  topic: string;
  confidence: number;
}

export interface ArchetypeWeight {
  name: string;
  baseline_weight: number;
  current_weight: number;
  accuracy: number;
  total_predictions: number;
}

export interface DailySimStats {
  total_runs: number;
  last_run_date: string;
  simulations_today: number;
  sim_hour_utc: number;
  max_sims_per_day: number;
}

export interface DailySimEntry {
  market_id: string;
  market_title: string;
  simulation_id: string;
  status: string;
  consensus: string;
  ran_at: string;
}
