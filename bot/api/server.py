from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from bot.config import settings
from bot.core.engine import TradingEngine
from bot.utils.db import Database
from bot.utils.log_broadcaster import broadcaster

app = FastAPI(title="MaxTheTrader API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your Vercel domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# These get set by main.py before the server starts
_engine: TradingEngine | None = None
_db: Database | None = None


def set_engine(engine: TradingEngine):
    global _engine, _db
    _engine = engine
    _db = engine.db


def set_database(db: Database):
    global _db
    _db = db


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_claude_registry() -> dict:
    registry_path = _repo_root() / ".Claude" / "agents" / "registry.json"
    if not registry_path.exists():
        return {}
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _empty_portfolio_state() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "balance": 0.0,
        "total_value": 0.0,
        "total_exposure": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "num_positions": 0,
        "timestamp": now,
        "positions": [],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "paper_mode": settings.PAPER_MODE}


@app.get("/api/portfolio")
async def portfolio():
    if not _engine:
        latest = await _db.get_portfolio_snapshot_latest() if _db else None
        if latest:
            return {
                "balance": latest.get("balance", 0.0),
                "total_value": latest.get("balance", 0.0)
                + latest.get("total_exposure", 0.0)
                + latest.get("unrealized_pnl", 0.0),
                "total_exposure": latest.get("total_exposure", 0.0),
                "unrealized_pnl": latest.get("unrealized_pnl", 0.0),
                "realized_pnl": latest.get("realized_pnl", 0.0),
                "num_positions": latest.get("num_positions", 0),
                "timestamp": latest.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "positions": [],
            }
        return _empty_portfolio_state()
    state = await _engine.portfolio.get_state(_engine.exchange)
    return state.to_dict()


@app.get("/api/portfolio/history")
async def portfolio_history(limit: int = 100):
    if not _db:
        return []
    return await _db.get_portfolio_history(limit=limit)


@app.get("/api/positions")
async def positions():
    if not _engine:
        return []
    pos = await _engine.exchange.get_positions()
    return [
        {
            "market_id": p.market_id,
            "market_title": p.market_title,
            "outcome": p.outcome,
            "size": p.size,
            "avg_entry_price": p.avg_entry_price,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "exchange": p.exchange,
        }
        for p in pos
    ]


@app.get("/api/trades")
async def trades(limit: int = 100, exchange: str = "", agent_id: str = ""):
    if not _db:
        return []
    return await _db.get_trades(limit=limit, exchange=exchange, agent_id=agent_id)


@app.get("/api/signals")
async def signals(limit: int = 50, agent_id: str = ""):
    if not _engine:
        return []
    # Combine recent engine signals with DB history
    recent = _engine.get_recent_signals(limit=limit)
    if agent_id:
        recent = [s for s in recent if s.get("agent_id") == agent_id]
    if recent:
        return recent[:limit]
    if _db:
        return await _db.get_signals(limit=limit, agent_id=agent_id)
    return []
    
@app.get("/api/agents/quality-gates")
async def agent_quality_gates():
    if not _engine:
        return {}
    return {
        agent_id: gate.to_dict()
        for agent_id, gate in getattr(_engine, "_quality_gates", {}).items()
    }


@app.get("/api/agents/registry")
async def agent_registry():
    if not _engine:
        return []
    return [
        {
            "agent_id": spec.agent_id,
            "market_type": spec.market_type,
            "exchange": spec.exchange,
            "timeframe": spec.timeframe,
            "enabled": spec.enabled,
            "runner": spec.runner,
            "notes": spec.notes,
        }
        for spec in getattr(_engine, "agent_registry", [])
    ]


@app.get("/api/markets")
async def markets(query: str = "", limit: int = 20):
    if not _engine:
        return []
    results = await _engine.market_data.search_markets(query=query, limit=limit)
    return [
        {
            "id": m.id,
            "title": m.title,
            "exchange": m.exchange,
            "prices": m.prices,
            "volume": m.volume,
            "category": m.category,
        }
        for m in results
    ]


@app.get("/api/markets/watchlist")
async def watchlist():
    if not _engine:
        return {}
    snapshots = _engine.market_data.get_all_snapshots()
    return {
        mid: {
            "id": s.market.id,
            "title": s.market.title,
            "exchange": s.market.exchange,
            "prices": s.market.prices,
            "volume": s.market.volume,
            "auto_discovered": s.auto_discovered,
            "price_history": [
                {"time": t.isoformat(), "price": p}
                for t, p in s.price_history[-50:]
            ],
        }
        for mid, s in snapshots.items()
    }


@app.post("/api/watchlist/{market_id}")
async def add_to_watchlist(market_id: str):
    if not _engine:
        return {"error": "Engine not running"}
    _engine.market_data.add_to_watchlist(market_id)
    return {"status": "added", "market_id": market_id}


@app.delete("/api/watchlist/{market_id}")
async def remove_from_watchlist(market_id: str):
    if not _engine:
        return {"error": "Engine not running"}
    _engine.market_data.remove_from_watchlist(market_id)
    return {"status": "removed", "market_id": market_id}


@app.get("/api/balance")
async def balance():
    if not _engine:
        return {"balance": 0}
    bal = await _engine.exchange.get_balance()
    return {"balance": bal}


@app.get("/api/telegram/inputs")
async def telegram_inputs(limit: int = 20, agent_id: str = ""):
    if not _db:
        return []
    return await _db.get_agent_inputs(limit=limit, agent_id=agent_id)


@app.get("/api/orchestration/status")
async def orchestration_status():
    if not _engine:
        now = datetime.now(timezone.utc)
        claude_registry = _load_claude_registry()
        trader_configs = claude_registry.get("traders", []) if isinstance(claude_registry, dict) else []
        return {
            "timestamp": now.isoformat(),
            "orchestrator": {
                "engine_running": False,
                "cycle_count": 0,
                "poll_interval_seconds": None,
                "snapshot_interval_seconds": None,
                "active_runtime_agents": 0,
                "recent_signal_count": 0,
            },
            "learning_pipeline": {
                "builder": {
                    "agent_id": claude_registry.get("learning_pipeline", {}).get("builder", {}).get("id", "knowledge_builder"),
                    "cadence_seconds": claude_registry.get("learning_pipeline", {}).get("builder", {}).get("cadence_seconds"),
                },
                "validator": {
                    "agent_id": claude_registry.get("learning_pipeline", {}).get("validator", {}).get("id", "knowledge_validator"),
                    "cadence_seconds": claude_registry.get("learning_pipeline", {}).get("validator", {}).get("cadence_seconds"),
                },
                "contracts_file": claude_registry.get("contracts_file"),
                "registry_version": claude_registry.get("version"),
            },
            "traders": [
                {
                    "agent_id": str(t.get("id", "")),
                    "enabled": bool(t.get("enabled", True)),
                    "cadence_seconds": t.get("cadence_seconds"),
                    "runner": None,
                    "exchange": None,
                    "timeframe": None,
                    "market_type": None,
                    "signals_total": 0,
                    "signals_last_1h": 0,
                    "trades_total": 0,
                    "trades_last_24h": 0,
                    "recent_engine_signals": 0,
                    "last_signal_at": None,
                    "last_signal_reasoning": None,
                    "last_trade_at": None,
                    "quality_gate": None,
                }
                for t in trader_configs
                if t.get("id")
            ],
        }

    now = datetime.now(timezone.utc)
    claude_registry = _load_claude_registry()
    trader_configs = claude_registry.get("traders", []) if isinstance(claude_registry, dict) else []
    trader_ids = [str(t.get("id", "")) for t in trader_configs if t.get("id")]

    # Include runtime-registered agents as well, even if not present in .Claude registry.
    runtime_registry = getattr(_engine, "agent_registry", []) or []
    for spec in runtime_registry:
        if spec.agent_id not in trader_ids:
            trader_ids.append(spec.agent_id)

    # Pull wider windows once and aggregate in-memory to keep this endpoint fast.
    db_signals = await _db.get_signals(limit=500) if _db else []
    db_trades = await _db.get_trades(limit=500) if _db else []
    recent_engine_signals = _engine.get_recent_signals(limit=200)

    by_agent_signal_total: dict[str, int] = defaultdict(int)
    by_agent_signal_1h: dict[str, int] = defaultdict(int)
    by_agent_last_signal_ts: dict[str, str] = {}
    by_agent_last_signal_reasoning: dict[str, str] = {}

    for signal in db_signals:
        agent_id = signal.get("agent_id") or "core_engine"
        by_agent_signal_total[agent_id] += 1
        signal_time = _safe_iso_to_dt(signal.get("created_at") or signal.get("timestamp"))
        if signal_time and signal_time >= now - timedelta(hours=1):
            by_agent_signal_1h[agent_id] += 1
        if agent_id not in by_agent_last_signal_ts:
            by_agent_last_signal_ts[agent_id] = signal.get("created_at") or signal.get("timestamp") or ""
            by_agent_last_signal_reasoning[agent_id] = signal.get("reasoning", "")

    by_agent_trade_total: dict[str, int] = defaultdict(int)
    by_agent_trade_24h: dict[str, int] = defaultdict(int)
    by_agent_last_trade_ts: dict[str, str] = {}

    for trade in db_trades:
        agent_id = trade.get("agent_id") or "core_engine"
        by_agent_trade_total[agent_id] += 1
        trade_time = _safe_iso_to_dt(trade.get("created_at"))
        if trade_time and trade_time >= now - timedelta(hours=24):
            by_agent_trade_24h[agent_id] += 1
        if agent_id not in by_agent_last_trade_ts:
            by_agent_last_trade_ts[agent_id] = trade.get("created_at") or ""

    recent_signal_distribution: dict[str, int] = defaultdict(int)
    for signal in recent_engine_signals:
        recent_signal_distribution[signal.get("agent_id") or "core_engine"] += 1

    quality_gates = {
        agent_id: gate.to_dict()
        for agent_id, gate in getattr(_engine, "_quality_gates", {}).items()
    }

    runtime_specs: dict[str, dict] = {
        spec.agent_id: {
            "market_type": spec.market_type,
            "exchange": spec.exchange,
            "timeframe": spec.timeframe,
            "enabled": spec.enabled,
            "runner": spec.runner,
            "notes": spec.notes,
        }
        for spec in runtime_registry
    }

    trader_statuses = []
    for trader_id in trader_ids:
        conf = next((t for t in trader_configs if t.get("id") == trader_id), {})
        runtime = runtime_specs.get(trader_id, {})
        trader_statuses.append(
            {
                "agent_id": trader_id,
                "enabled": runtime.get("enabled", conf.get("enabled", True)),
                "cadence_seconds": conf.get("cadence_seconds"),
                "runner": runtime.get("runner"),
                "exchange": runtime.get("exchange"),
                "timeframe": runtime.get("timeframe"),
                "market_type": runtime.get("market_type"),
                "signals_total": by_agent_signal_total.get(trader_id, 0),
                "signals_last_1h": by_agent_signal_1h.get(trader_id, 0),
                "trades_total": by_agent_trade_total.get(trader_id, 0),
                "trades_last_24h": by_agent_trade_24h.get(trader_id, 0),
                "recent_engine_signals": recent_signal_distribution.get(trader_id, 0),
                "last_signal_at": by_agent_last_signal_ts.get(trader_id),
                "last_signal_reasoning": by_agent_last_signal_reasoning.get(trader_id),
                "last_trade_at": by_agent_last_trade_ts.get(trader_id),
                "quality_gate": quality_gates.get(trader_id),
            }
        )

    knowledge_builder_cfg = (
        claude_registry.get("learning_pipeline", {}).get("builder", {})
        if isinstance(claude_registry, dict)
        else {}
    )
    knowledge_validator_cfg = (
        claude_registry.get("learning_pipeline", {}).get("validator", {})
        if isinstance(claude_registry, dict)
        else {}
    )

    return {
        "timestamp": now.isoformat(),
        "orchestrator": {
            "engine_running": bool(getattr(_engine, "_running", False)),
            "cycle_count": getattr(_engine, "_cycle_count", 0),
            "poll_interval_seconds": getattr(_engine, "poll_interval", None),
            "snapshot_interval_seconds": getattr(_engine, "snapshot_interval", None),
            "active_runtime_agents": len([a for a in runtime_registry if a.enabled]),
            "recent_signal_count": len(recent_engine_signals),
        },
        "learning_pipeline": {
            "builder": {
                "agent_id": knowledge_builder_cfg.get("id", "knowledge_builder"),
                "cadence_seconds": knowledge_builder_cfg.get("cadence_seconds"),
            },
            "validator": {
                "agent_id": knowledge_validator_cfg.get("id", "knowledge_validator"),
                "cadence_seconds": knowledge_validator_cfg.get("cadence_seconds"),
            },
            "contracts_file": claude_registry.get("contracts_file"),
            "registry_version": claude_registry.get("version"),
        },
        "traders": trader_statuses,
    }


# ── Swarm Intelligence Endpoints ─────────────────────────────────────────

@app.post("/api/swarm/simulate")
async def swarm_simulate(topic: str = "", max_rounds: int = 5):
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    if not topic:
        return {"error": "topic is required"}

    from bot.swarm.simulation_manager import SimulationManager
    manager = SimulationManager()
    result = await manager.run_simulation(topic=topic, max_rounds=max_rounds)
    return {
        "simulation_id": result.simulation_id,
        "topic": result.topic,
        "status": result.status,
        "total_actions": result.total_actions,
        "num_rounds": result.num_rounds,
        "posts": result.posts[:20],
        "by_agent": result.by_agent,
        "by_type": result.by_type,
        "error": result.error,
    }


@app.get("/api/swarm/simulations")
async def swarm_list_simulations():
    if not settings.SWARM_ENABLED:
        return []
    from bot.swarm.simulation_manager import SimulationManager
    return SimulationManager().list_simulations()


@app.get("/api/swarm/status/{sim_id}")
async def swarm_sim_status(sim_id: str):
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.simulation_manager import SimulationManager
    status = SimulationManager().get_status(sim_id)
    return status or {"error": "Simulation not found"}


@app.get("/api/graph/stats")
async def graph_stats():
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.async_wrappers import SwarmGraphAsync
    swarm = SwarmGraphAsync()
    graph_id = await swarm.get_graph_id()
    if not graph_id:
        return {"error": "No knowledge graph available"}
    return await swarm.get_graph_statistics(graph_id)


@app.get("/api/graph/search")
async def graph_search(q: str = "", limit: int = 10):
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    if not q:
        return {"error": "q parameter is required"}
    from bot.swarm.async_wrappers import SwarmGraphAsync
    swarm = SwarmGraphAsync()
    graph_id = await swarm.get_graph_id()
    if not graph_id:
        return {"error": "No knowledge graph available"}
    result = await swarm.quick_search(graph_id, q, limit=limit)
    return result.to_dict()


@app.post("/api/swarm/analyze")
async def swarm_deep_analysis(topic: str = ""):
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    if not topic:
        return {"error": "topic is required"}
    from bot.swarm.deep_analysis import DeepAnalysisAgent
    agent = DeepAnalysisAgent()
    analysis = await agent.analyze(topic)
    return {
        "topic": analysis.topic,
        "thesis": analysis.thesis,
        "supporting_evidence": analysis.supporting_evidence,
        "risk_factors": analysis.risk_factors,
        "confidence": analysis.confidence,
        "recommended_action": analysis.recommended_action,
        "tool_calls_made": analysis.tool_calls_made,
        "elapsed_seconds": analysis.elapsed_seconds,
    }


# ── Phase 6: Auto-Triggers & Learning Loop Endpoints ──────────────────

@app.get("/api/accuracy/archetypes")
async def accuracy_archetypes():
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.accuracy_tracker import AccuracyTracker
    tracker = AccuracyTracker()
    await tracker.init()
    stats = await tracker.get_archetype_stats()
    return [
        {
            "archetype": s.archetype,
            "total_predictions": s.total_predictions,
            "correct_predictions": s.correct_predictions,
            "accuracy": s.accuracy,
            "avg_confidence": s.avg_confidence,
        }
        for s in stats
    ]


@app.get("/api/accuracy/predictions")
async def accuracy_predictions(limit: int = 50):
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.accuracy_tracker import AccuracyTracker
    tracker = AccuracyTracker()
    await tracker.init()
    return await tracker.get_prediction_history(limit=limit)


@app.get("/api/accuracy/simulation/{sim_id}")
async def accuracy_simulation(sim_id: str):
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.accuracy_tracker import AccuracyTracker
    tracker = AccuracyTracker()
    await tracker.init()
    return await tracker.get_simulation_accuracy(sim_id)


@app.get("/api/weights")
async def archetype_weights():
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.weight_adjuster import WeightAdjuster
    adjuster = WeightAdjuster()
    return await adjuster.get_current_weights()


@app.post("/api/weights/adjust")
async def adjust_weights():
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.weight_adjuster import WeightAdjuster
    adjuster = WeightAdjuster()
    changes = await adjuster.apply_adjustments()
    return {"changes": changes, "count": len(changes)}


@app.get("/api/daily-simulations")
async def daily_simulations():
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.scheduled_simulations import ScheduledSimulationRunner
    runner = ScheduledSimulationRunner()
    return {
        "stats": runner.get_stats(),
        "today_results": runner.get_today_results(),
    }


@app.post("/api/daily-simulations/run")
async def run_daily_simulations():
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    if not _engine:
        return {"error": "Engine not running"}
    from bot.swarm.scheduled_simulations import ScheduledSimulationRunner
    runner = ScheduledSimulationRunner(_engine.market_data)
    runner._state.last_run_date = ""  # Force run
    results = await runner.maybe_run_daily()
    return {
        "count": len(results),
        "results": [
            {
                "market_id": r.market_id,
                "market_title": r.market_title,
                "consensus": r.consensus,
                "bullish": r.bullish_count,
                "bearish": r.bearish_count,
                "status": r.status,
            }
            for r in results
        ],
    }


@app.get("/api/event-trigger/score")
async def event_trigger_score(summary: str = "", content_type: str = "news"):
    """Test the event trigger scoring on arbitrary input."""
    if not settings.SWARM_ENABLED:
        return {"error": "Swarm features disabled"}
    from bot.swarm.event_trigger import EventTrigger
    trigger = EventTrigger()
    trigger.reset_cooldown()
    score = trigger.score_impact({
        "summary": summary,
        "content_type": content_type,
        "key_topics": [],
        "relevant_markets": [],
        "original_content": summary,
    })
    return score.to_dict()


# ── Polymarket Copy Trading ───────────────────────────────────────────────

@app.get("/api/copy/leaders")
async def copy_leaders():
    if not _db:
        return []
    stats = await _db.get_copy_leaders_stats()
    configured = [
        {"wallet": w, "configured": True, "name": w[:10]}
        for w in settings.POLY_COPY_LEADERS
    ]
    seen_wallets = {s["leader_wallet"] for s in stats}
    for leader in configured:
        if leader["wallet"] not in seen_wallets:
            stats.append({
                "leader_wallet": leader["wallet"],
                "trades_seen": 0,
                "trades_mirrored": 0,
                "last_seen_at": None,
                "configured": True,
            })
    for row in stats:
        row["configured"] = row["leader_wallet"] in settings.POLY_COPY_LEADERS
    return stats


@app.get("/api/copy/trades")
async def copy_trades(limit: int = 100, leader_wallet: str = ""):
    if not _db:
        return []
    return await _db.get_copy_trades(limit=limit, leader_wallet=leader_wallet)


@app.get("/api/copy/stats")
async def copy_stats():
    if not _db:
        return {"error": "Database not available"}
    stats = await _db.get_copy_stats()
    stats["leaders_configured"] = len(settings.POLY_COPY_LEADERS)
    stats["poll_interval_seconds"] = settings.POLY_COPY_POLL_INTERVAL
    stats["sizing_mode"] = settings.POLY_COPY_SIZING_MODE
    stats["fixed_usd"] = settings.POLY_COPY_FIXED_USD
    stats["max_lag_seconds"] = settings.POLY_COPY_MAX_LAG_SECONDS
    stats["agent_enabled"] = settings.AGENT_ENABLE_POLYMARKET_COPY
    if _engine and getattr(_engine, "polymarket_copy_agent", None):
        stats["last_poll_at"] = (
            _engine.polymarket_copy_agent._last_poll_at.isoformat()
            if _engine.polymarket_copy_agent._last_poll_at
            else None
        )
    return stats


@app.get("/api/copy/discover")
async def copy_discover(limit: int = 10):
    """Suggest active wallets from recent Polymarket trades."""
    from bot.data.polymarket_data import PolymarketDataClient
    client = PolymarketDataClient()
    try:
        return await client.discover_active_wallets(limit=limit)
    finally:
        await client.close()


@app.get("/api/reports/daily")
async def daily_reports(limit: int = 30):
    if not _db:
        return []
    return await _db.get_daily_pnl_reports(limit=limit)


@app.post("/api/reports/daily/run")
async def run_daily_report():
    """Generate today's P&L report on demand."""
    from bot.monitoring.daily_pnl_report import DailyPnLReporter
    reporter = DailyPnLReporter(_db)
    report = await reporter.generate()
    if settings.DAILY_REPORT_TELEGRAM:
        await reporter.deliver_telegram(report)
    return report.to_dict()


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    await broadcaster.add_connection(websocket)
    # Send catch-up buffer immediately so new clients see recent history
    for line in broadcaster.get_recent(200):
        try:
            await websocket.send_text(line)
        except Exception:
            break
    try:
        while True:
            # Keep connection alive — client sends pings
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError is raised by starlette when the client disconnects
        # abruptly (e.g. page refresh) before the receive handshake completes.
        pass
    finally:
        await broadcaster.remove_connection(websocket)
