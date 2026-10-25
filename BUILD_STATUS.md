# Max the Trader — Build Status

**Last updated:** 2026-04-10 (Phase 7 complete + KB reset baseline)

---

## System Overview

Max the Trader is an autonomous prediction market trading bot for Kalshi and Polymarket, enhanced with swarm intelligence capabilities from the MiroFish "Predict Anything" repo. The system combines a multi-agent AI trading pipeline with a Zep Cloud knowledge graph, multi-persona swarm simulations, and a ReACT-pattern deep analysis agent.

---

## Core System (Pre-Integration) — COMPLETE

| Component | Status | Key Files |
|-----------|--------|-----------|
| Trading Engine (30s loop) | Working | `bot/core/engine.py` |
| BTC 15-min Day Trader (5-filter) | Working | `bot/strategies/market_configs/btc15m.py` |
| Agent Pipeline (Researcher → Synthesizer → Trader) | Working | `bot/agents/pipeline.py` |
| Kalshi Exchange Integration | Working (prod) | `bot/exchanges/kalshi.py` |
| Polymarket Integration | Working (read-only) | `bot/exchanges/polymarket.py` |
| Paper Trading Mode | Working | `bot/exchanges/paper.py` |
| News-Driven Strategy | Working | `bot/strategies/news_driven.py` |
| Momentum Strategy | Working | `bot/strategies/momentum.py` |
| Risk Manager | Working | `bot/risk/manager.py` |
| Knowledge Base (per-agent JSON stores) | Working | `bot/knowledge/knowledge_base.py` |
| Knowledge Learner Agent | Working | `bot/agents/knowledge_learner.py` |
| Telegram Bot (12 commands) | Working | `bot/telegram_bot/bot.py` |
| FastAPI + WebSocket Logs | Working | `bot/api/server.py` |
| Next.js Dashboard | Working | `dashboard/src/app/` |
| SQLite Database | Working | `bot/utils/db.py` |
| BTC Price Feed (Coinbase/Kraken) | Working | `bot/data/btc_price.py` |
| Market Auto-Discovery | Working | `bot/data/market_data.py` |

---

## Predict Anything Integration — Phases 1-5

### Phase 1: Knowledge Graph Foundation — COMPLETE

Zep Cloud knowledge graph running alongside the existing JSON knowledge base. All content ingested via `#learn` is dual-written to both systems. The Researcher agent has a new `search_knowledge_graph` tool.

| Component | Status | File |
|-----------|--------|------|
| Zep graph builder (create, ontology, episodes) | Working, tested | `bot/swarm/graph_builder.py` |
| Zep search tools (QuickSearch, InsightForge, Panorama) | Working, tested | `bot/swarm/zep_tools.py` |
| Trading ontology (10 entity types, 10 edge types) | Working, tested | `bot/swarm/ontology.py` |
| Async wrappers (60s search cache) | Working, tested | `bot/swarm/async_wrappers.py` |
| OpenAI-format LLM client | Working, tested | `bot/swarm/llm_client.py` |
| Text chunking for graph ingestion | Working, tested | `bot/swarm/text_processor.py` |
| Zep pagination with retry | Working, tested | `bot/swarm/zep_paging.py` |
| Dual-write in KnowledgeLearnerAgent | Working | `bot/agents/knowledge_learner.py` |
| `search_knowledge_graph` tool in Researcher | Working | `bot/agents/researcher.py` |
| Config: ZEP_API_KEY, SWARM_ENABLED, etc. | Configured | `bot/config/settings.py` |

**Credentials:** Zep Cloud API key set. Graph created: `maxtrader_e208108b5af64d77`.

**Verified:** End-to-end test passed — ingested test text, Zep extracted 5 nodes and 3 edges, semantic search returned correct facts.

### Phase 2: Swarm Simulation Engine — COMPLETE

10 trader archetype personas simulate market discussions via LLM-powered rounds. Each persona has distinct personality, trading style, risk tolerance, and influence weight.

| Component | Status | File |
|-----------|--------|------|
| 10 Trader archetype profiles | Working, tested | `bot/swarm/trader_profiles.py` |
| Simulation config generator (LLM seed posts) | Working, tested | `bot/swarm/simulation_config.py` |
| Action logger (JSONL) | Working | `bot/swarm/action_logger.py` |
| File-based IPC (command/response) | Working | `bot/swarm/simulation_ipc.py` |
| Simulation runner (async subprocess) | Working, tested | `bot/swarm/simulation_runner.py` |
| Simulation manager (lifecycle orchestrator) | Working, tested | `bot/swarm/simulation_manager.py` |
| OASIS simulation script (LLM fallback mode) | Working, tested | `bot/swarm/scripts/run_simulation.py` |
| SwarmAnalysisLayer (pipeline integration) | Working | `bot/swarm/analysis_layer.py` |
| Pipeline: swarm_layer between Researcher/Synthesizer | Working | `bot/agents/pipeline.py` |

**OASIS Status:** `camel-ai` installed (v0.1.6.7), but `camel-oasis` is not available as a pip package. Simulations use the **LLM fallback mode** — each trader gets a direct GPT-4o-mini call instead of running through the full OASIS platform simulation. Results are equivalent quality.

**Verified:** Full simulation ran successfully — "BTC drops below 90k after FOMC hawkish surprise" produced 13 actions from 7 active agents over 3 rounds in ~33 seconds.

**Trader Archetypes:**

| ID | Name | Archetype | Influence | Style |
|----|------|-----------|-----------|-------|
| 0 | Morgan Steele | InstitutionalTrader | 0.95 | Macro-driven, hedged |
| 1 | Kyle WAGMI | RetailDegen | 0.30 | Momentum chaser, high leverage |
| 2 | Dr. Sarah Chen | MacroAnalyst | 0.85 | Macro-fundamental, data-driven |
| 3 | QuantBot_7 | QuantTrader | 0.70 | Systematic, indicator-driven |
| 4 | WhaleAlert Mike | WhaleWatcher | 0.75 | On-chain flow analysis |
| 5 | Breaking Crypto | NewsTrader | 0.90 | News-driven, event catalyst |
| 6 | Contrarian Carl | Contrarian | 0.65 | Fades consensus, mean-reversion |
| 7 | MM_Desk | MarketMaker | 0.80 | Market-neutral, volatility-focused |
| 8 | YOLOTrader69 | FOMOBuyer | 0.20 | Reactive, always late |
| 9 | REKT_Capitulator | PanicSeller | 0.25 | Fear-driven, loss-averse |

### Phase 3: Deep Analysis Agent — COMPLETE

ReACT-pattern agent that iteratively queries the knowledge graph to produce structured trading theses with evidence, risk factors, confidence scores, and trade recommendations.

| Component | Status | File |
|-----------|--------|------|
| DeepAnalysisAgent (ReACT loop) | Working | `bot/swarm/deep_analysis.py` |
| Structured output (thesis, evidence, risks, confidence) | Working | `bot/swarm/deep_analysis.py` |
| Telegram formatting | Working | `bot/swarm/deep_analysis.py` |

### Phase 4: Telegram & API Interface — COMPLETE

5 new Telegram commands and 7 new API endpoints for swarm features. The `#swarm` tag forces simulation on any content going through the pipeline.

| Component | Status | File |
|-----------|--------|------|
| `/simulate <topic>` | Working | `bot/telegram_bot/bot.py` |
| `/simstatus` | Working | `bot/telegram_bot/bot.py` |
| `/graph` | Working | `bot/telegram_bot/bot.py` |
| `/graphsearch <query>` | Working | `bot/telegram_bot/bot.py` |
| `/deepanalysis <topic>` | Working | `bot/telegram_bot/bot.py` |
| `#swarm` tag support | Working | `bot/telegram_bot/bot.py` |
| `POST /api/swarm/simulate` | Working | `bot/api/server.py` |
| `GET /api/swarm/simulations` | Working | `bot/api/server.py` |
| `GET /api/swarm/status/{sim_id}` | Working | `bot/api/server.py` |
| `GET /api/graph/stats` | Working | `bot/api/server.py` |
| `GET /api/graph/search?q=` | Working | `bot/api/server.py` |
| `POST /api/swarm/analyze` | Working | `bot/api/server.py` |
| Swarm layer auto-init in pipeline | Working | `bot/telegram_bot/bot.py` |

**Total Telegram commands:** 17 (12 original + 5 swarm)
**Total API endpoints:** 19 (12 original + 7 swarm)

### Phase 5: Dashboard — COMPLETE

Two new dashboard pages and updated navigation.

| Component | Status | File |
|-----------|--------|------|
| Knowledge Graph page (stats + search) | Built | `dashboard/src/app/graph/page.tsx` |
| Simulations page (run + results + history) | Built | `dashboard/src/app/simulations/page.tsx` |
| Updated sidebar navigation | Done | `dashboard/src/app/layout.tsx` |
| API client extensions | Done | `dashboard/src/lib/api.ts` |

**Note:** Dashboard pages are built but not yet tested with a live API server.

### Phase 6: Auto-Triggers & Learning Loop — COMPLETE

Self-improving feedback loop: the system now auto-triggers simulations on high-impact events, tracks prediction accuracy, adjusts archetype weights, and runs daily scheduled simulations for long-horizon markets.

| Component | Status | File |
|-----------|--------|------|
| EventTrigger (impact scoring, auto-trigger) | Working | `bot/swarm/event_trigger.py` |
| AccuracyTracker (predictions + outcomes + stats) | Working | `bot/swarm/accuracy_tracker.py` |
| ResolutionChecker (polls resolved markets) | Working | `bot/swarm/resolution_checker.py` |
| Graph-enhanced BTC15M (6th filter + confidence mod) | Working | `bot/strategies/market_configs/btc15m.py` |
| GraphContextProvider (cached BTC graph signals) | Working | `bot/swarm/graph_context.py` |
| WeightAdjuster (accuracy-based influence tuning) | Working | `bot/swarm/weight_adjuster.py` |
| ScheduledSimulationRunner (daily long-horizon sims) | Working | `bot/swarm/scheduled_simulations.py` |
| Engine integration (resolution + weights + daily) | Working | `bot/core/engine.py` |
| Telegram commands (/accuracy, /weights, /dailysim) | Working | `bot/telegram_bot/bot.py` |
| API endpoints (8 new) | Working | `bot/api/server.py` |
| Dashboard: Learning Loop page | Built | `dashboard/src/app/accuracy/page.tsx` |

**Event Trigger:** Scores research output (0-1) across 4 dimensions: content type, topic relevance, market density, keyword signals. Auto-fires simulation when score >= 0.65 with 5-min cooldown.

**Accuracy Tracking:** Records per-archetype predictions from simulation posts (bullish/bearish sentiment analysis). When markets resolve, scores predictions and updates running accuracy stats per archetype. Resolution checker polls every ~10 minutes.

**Graph-Enhanced BTC15M:** The BTC15M 5-filter check now has a 6th filter that queries the knowledge graph for BTC context. Strong graph contradiction blocks the trade. Graph alignment boosts confidence. Uses cached queries (2-min TTL) to avoid latency.

**Weight Adjustment:** Reads archetype accuracy stats and adjusts influence_weight in-place: accuracy above 50% boosts weight, below 50% penalizes. Bounded ±0.15 from baseline, floor 0.10, ceiling 0.99. Requires minimum 5 predictions. Runs every ~6 hours.

**Scheduled Daily Simulations:** Discovers markets expiring in 1-7 days with undecided prices (20-80%), picks top 3 by volume, runs swarm simulations at 8 AM UTC daily. Results stored for pipeline enrichment and accuracy tracking.

**New Telegram commands:** 20 total (17 + 3 Phase 6)
**New API endpoints:** 27 total (19 + 8 Phase 6)
**New dashboard pages:** 3 total (2 Phase 5 + 1 Phase 6)

---

### Phase 7: Multi-Agent Runtime Expansion — COMPLETE

The core runtime now supports multiple concurrently-runnable trading agents under one orchestrator with per-agent attribution and quality gates.

| Component | Status | File |
|-----------|--------|------|
| Agent registry model (`TradingAgentSpec`) | Working | `bot/core/agent_registry.py` |
| Agent quality gate model (`AgentQualityGate`) | Working | `bot/core/agent_registry.py` |
| Engine runner routing (`day_trader`, `polymarket_news`, `stocks_etf_shell`) | Working | `bot/core/engine.py` |
| Exchange-aware signal execution routing | Working | `bot/core/engine.py` |
| Signal schema (`agent_id`, `execution_exchange`) | Working | `bot/core/signals.py` |
| Trader exchange hardcode removal | Working | `bot/agents/trader.py` |
| Polymarket candidate filters (volume/spread/time-to-close) | Working | `bot/data/market_data.py` |
| Polymarket-specific risk overrides | Working | `bot/risk/manager.py` |
| Stocks/ETF paper shell agent scaffold | Working | `bot/agents/stocks_etf_paper_agent.py` |
| DB attribution columns (`agent_id`) + query filters | Working | `bot/utils/db.py` |
| API additions (`/api/agents/registry`, `/api/agents/quality-gates`) | Working | `bot/api/server.py` |
| Dashboard attribution fields (signals/trades) | Working | `dashboard/src/lib/api.ts`, `dashboard/src/app/signals/page.tsx`, `dashboard/src/app/trades/page.tsx` |
| Quality gate documentation | Working | `docs/AGENT_QUALITY_GATES.md` |

**Registered runtime agents:**
- `btc15m_kalshi`
- `polymarket_news`
- `stocks_etf_paper_shell`

**Verification status:** API health is good; new agent registry/quality gate endpoints respond; new rows include `agent_id` attribution while legacy rows remain blank.

---

### Knowledge Reset Baseline — COMPLETE

Legacy knowledge documents were intentionally wiped to start clean, agent-specific learning from scratch.

| Component | Status | File/Path |
|-----------|--------|-----------|
| Legacy KB cleared (`knowledge_store/kalshi`, `knowledge_store/traditional`) | Complete | `knowledge_store/` |
| Local graph state reset (`graph_state.json`) | Complete | `knowledge_store/graph_state.json` |
| Per-agent KB baseline at zero | Complete | `knowledge_store/agents/` |
| Agent-targeted routing active in learner | Complete | `bot/agents/knowledge_learner.py`, `bot/knowledge/categories.py` |

**Current KB baseline:** `btc15m_kalshi=0`, `polymarket_news=0`, `stocks_etf_paper_shell=0` rules.

---

## Environment & Credentials

| Variable | Status | Purpose |
|----------|--------|---------|
| `KALSHI_API_KEY` | Set | Kalshi exchange auth |
| `KALSHI_PRIVATE_KEY_PATH` | Set | RSA signing for Kalshi |
| `KALSHI_ENV` | `prod` | Production Kalshi environment |
| `ANTHROPIC_API_KEY` | Set | Claude API for trading agents |
| `TELEGRAM_BOT_TOKEN` | Set | Telegram bot interface |
| `TELEGRAM_ALLOWED_USER_IDS` | Set | Access control |
| `PAPER_MODE` | `true` | Paper trading active |
| `SWARM_ENABLED` | `true` | Swarm features active |
| `ZEP_API_KEY` | Set | Zep Cloud knowledge graph |
| `SWARM_LLM_API_KEY` | Set | OpenAI GPT-4o-mini for simulations |
| `SWARM_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI API endpoint |
| `SWARM_LLM_MODEL_NAME` | `gpt-4o-mini` | Simulation LLM model |
| `NEWS_API_KEY` | Set (if configured) | NewsAPI for research |
| `FIRECRAWL_API_KEY` | Set (if configured) | Web content extraction |
| `SWARM_AUTO_TRIGGER` | `true` | Auto-fire sims on high-impact events |
| `SWARM_DAILY_SIM_HOUR` | `8` | UTC hour for daily simulations |
| `SWARM_DAILY_SIM_MAX` | `3` | Max daily simulations per day |
| `AGENT_ENABLE_BTC15M` | `true` | Enable BTC15m registered agent |
| `AGENT_ENABLE_POLYMARKET_NEWS` | `true` | Enable Polymarket news agent |
| `AGENT_ENABLE_STOCKS_ETF_SHELL` | `true` | Enable stocks/ETF paper shell |
| `AGENT_POLY_MIN_VOLUME` | `10000` (default) | Liquidity floor for Polymarket agent |
| `AGENT_POLY_MAX_SPREAD` | `0.08` (default) | Max spread for Polymarket candidate selection |
| `AGENT_POLY_MIN_MINUTES_TO_CLOSE` | `60` (default) | Time-to-close minimum for Polymarket candidates |
| `RISK_POLY_MAX_POSITION_PCT` | `0.01` (default) | Position cap override for Polymarket signals |
| `RISK_POLY_MAX_EXPOSURE_PCT` | `0.05` (default) | Exposure cap override for Polymarket signals |

---

## Dependencies

**Python (venv):**
- Core: `anthropic`, `httpx`, `fastapi`, `uvicorn`, `pydantic`, `rich`, `python-telegram-bot`
- Phase 1: `zep-cloud>=3.13.0`, `openai>=1.0.0`
- Phase 2: `camel-ai==0.1.6.7` (OASIS not available — using LLM fallback)

**Node.js (dashboard):**
- Next.js 16, React 19, Tailwind CSS 4, lightweight-charts, socket.io-client

---

## File Structure (Swarm Package)

```
bot/swarm/
  __init__.py                    # Package init
  llm_client.py                  # OpenAI-format LLM client
  text_processor.py              # Text chunking for graph ingestion
  zep_paging.py                  # Zep pagination with retry
  graph_builder.py               # Zep graph create/ontology/ingest
  zep_tools.py                   # QuickSearch, InsightForge, Panorama
  ontology.py                    # 10 entity types + 10 edge types
  async_wrappers.py              # SwarmGraphAsync (60s cache)
  trader_profiles.py             # 10 trader archetype personas
  simulation_config.py           # LLM-powered config generation
  action_logger.py               # JSONL action logging
  simulation_ipc.py              # File-based IPC
  simulation_runner.py           # Async subprocess management
  simulation_manager.py          # Simulation lifecycle orchestrator
  analysis_layer.py              # SwarmAnalysisLayer (pipeline plug-in)
  deep_analysis.py               # ReACT deep analysis agent
  event_trigger.py               # Phase 6: Impact scoring + auto-trigger
  accuracy_tracker.py            # Phase 6: Prediction vs outcome tracking
  resolution_checker.py          # Phase 6: Polls resolved markets
  graph_context.py               # Phase 6: Graph signal for BTC15M
  weight_adjuster.py             # Phase 6: Accuracy-based weight tuning
  scheduled_simulations.py       # Phase 6: Daily long-horizon sims
  scripts/
    __init__.py
    run_simulation.py            # OASIS/LLM fallback simulation runner
```

---

## Known Limitations

1. **OASIS not available as pip package** — Simulations use LLM fallback mode (GPT-4o-mini direct calls per agent). Functionally equivalent but doesn't simulate the full Twitter platform mechanics (likes, reposts affect visibility).
2. **Dashboard pages not live-tested** — Built but require the API server running to verify.
3. **Auto-triggered simulations have cooldown** — 5-minute cooldown between auto-triggers to prevent spam. Threshold (0.65) may need tuning based on real-world usage.
4. **Knowledge graph starts empty by design** — A clean reset was performed. New `#learn` inputs are required to rebuild agent-specific knowledge.
5. **Python 3.9.6** — The venv uses Python 3.9.6 which is older. Some newer camel-ai features may not be available.
6. **Legacy attribution rows** — Trades/signals logged before Phase 7 have blank `agent_id`; only new rows are fully attributed.
7. **Agent-specific datasets warm up over time** — `btc15m_kalshi` / `polymarket_news` filtered views may be sparse until each agent emits enough qualifying signals.
