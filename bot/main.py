#!/usr/bin/env python3
"""MaxTheTrader - Automated prediction market trading bot."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bot.config import settings
from bot.core.agent_registry import AgentQualityGate, TradingAgentSpec
from bot.core.engine import TradingEngine
from bot.core.portfolio import PortfolioTracker
from bot.data.approved_markets import ApprovedMarketsStore
from bot.data.market_data import MarketDataFetcher
from bot.data.news import NewsCollector
from bot.exchanges.base import Exchange
from bot.exchanges.kalshi import KalshiExchange
from bot.exchanges.paper import PaperExchange
from bot.exchanges.polymarket import PolymarketExchange
from bot.risk.manager import RiskManager
from bot.strategies.momentum import MomentumStrategy
from bot.strategies.news_driven import NewsDrivenStrategy
from bot.utils.db import Database
from bot.utils.logger import log

# Project root — approved_markets.json lives here alongside .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def build_exchange(name: str, paper: bool) -> Exchange:
    """Create exchange instance, optionally wrapped in paper trading."""
    if name == "kalshi":
        real = KalshiExchange()
    elif name == "polymarket":
        real = PolymarketExchange()
    else:
        raise ValueError(f"Unknown exchange: {name}")

    if paper:
        return PaperExchange(real, initial_balance=settings.INITIAL_BALANCE)
    return real


def build_strategies(names: list[str]):
    """Create strategy instances."""
    available = {
        "news": NewsDrivenStrategy(),
        "momentum": MomentumStrategy(),
    }
    if "all" in names:
        return list(available.values())
    return [available[n] for n in names if n in available]


async def run_bot(args):
    """Main bot entry point."""

    paper = args.mode == "paper"

    if args.mode == "live" and settings.PAPER_MODE:
        log.warning(
            "[yellow]Live mode requested via CLI; ignoring PAPER_MODE=true from .env "
            "for this session.[/]"
        )

    # Build exchanges
    exchanges = []
    exchange_names = [name.strip() for name in args.exchange.split(",") if name.strip()]
    if settings.AGENT_ENABLE_POLYMARKET_NEWS and "polymarket" not in exchange_names:
        exchange_names.append("polymarket")
    if settings.AGENT_ENABLE_POLYMARKET_COPY and "polymarket" not in exchange_names:
        exchange_names.append("polymarket")

    for name in exchange_names:
        name = name.strip()
        ex = build_exchange(name, paper)
        exchanges.append(ex)

    # Use first exchange as primary for trading
    primary = exchanges[0]

    # Build components
    strategies = build_strategies(args.strategy.split(","))
    market_data = MarketDataFetcher(exchanges)
    news = NewsCollector()
    risk = RiskManager()
    db = Database()
    portfolio = PortfolioTracker(db)
    approved_markets = ApprovedMarketsStore(_PROJECT_ROOT / "approved_markets.json")
    exchange_map = {getattr(getattr(ex, "real", ex), "name", ex.name): ex for ex in exchanges}

    # Build day trader pipeline if requested
    day_trader_pipeline = None
    polymarket_copy_agent = None
    stocks_etf_agent = None
    if args.day_trader:
        from bot.agents.day_trader_researcher import DayTraderResearcherAgent
        from bot.agents.day_trader import DayTraderAgent
        from bot.agents.day_trader_pipeline import DayTraderPipeline
        from bot.strategies.market_configs.btc15m import get_btc15m_config

        # Get the underlying real exchange for market data fetching
        # (PaperExchange wraps the real one — we need the real client for API calls)
        real_exchange = primary.real if hasattr(primary, "real") else primary

        dt_researcher = DayTraderResearcherAgent(
            market_data=market_data,
            news_collector=news,
        )
        dt_trader = DayTraderAgent(exchange=primary)
        day_trader_pipeline = DayTraderPipeline(
            researcher=dt_researcher,
            day_trader=dt_trader,
            market_configs=[get_btc15m_config()],
            exchange_client=real_exchange,
        )
        log.info("[bold cyan]Day Trader pipeline initialized with BTC15M config[/]")

    if settings.AGENT_ENABLE_STOCKS_ETF_SHELL:
        from bot.agents.stocks_etf_paper_agent import StocksEtfPaperAgent
        stocks_etf_agent = StocksEtfPaperAgent()

    if settings.AGENT_ENABLE_POLYMARKET_COPY:
        from bot.agents.polymarket_copy_agent import PolymarketCopyAgent
        poly_exchange = exchange_map.get("polymarket") or primary
        polymarket_copy_agent = PolymarketCopyAgent(
            market_data=market_data,
            exchange=poly_exchange,
            db=db,
        )
        leaders = settings.POLY_COPY_LEADERS
        if leaders:
            log.info(f"[bold magenta]Copy Trader:[/] watching {len(leaders)} leader wallet(s)")
        else:
            log.warning(
                "[bold magenta]Copy Trader:[/] enabled but POLY_COPY_LEADERS is empty — "
                "add proxy wallet addresses to .env"
            )

    agent_registry: list[TradingAgentSpec] = [
        TradingAgentSpec(
            agent_id="btc15m_kalshi",
            market_type="kalshi_binary",
            exchange="kalshi",
            timeframe="15m",
            enabled=settings.AGENT_ENABLE_BTC15M and args.day_trader and day_trader_pipeline is not None,
            runner="day_trader",
            notes="BTC15M specialist config",
        ),
        TradingAgentSpec(
            agent_id="polymarket_news",
            market_type="polymarket_event",
            exchange="polymarket",
            timeframe="intraday",
            enabled=settings.AGENT_ENABLE_POLYMARKET_NEWS,
            runner="polymarket_news",
            notes="News + momentum conviction over filtered Polymarket watchlist",
        ),
        TradingAgentSpec(
            agent_id="polymarket_copy",
            market_type="polymarket_event",
            exchange="polymarket",
            timeframe="intraday",
            enabled=settings.AGENT_ENABLE_POLYMARKET_COPY and polymarket_copy_agent is not None,
            runner="polymarket_copy",
            notes="Mirror fills from configured leader proxy wallets",
        ),
        TradingAgentSpec(
            agent_id="stocks_etf_paper_shell",
            market_type="equities_etf",
            exchange="paper",
            timeframe="intraday",
            enabled=settings.AGENT_ENABLE_STOCKS_ETF_SHELL,
            runner="stocks_etf_shell",
            notes="Brokerless paper scaffold",
        ),
    ]

    quality_gates = {
        "btc15m_kalshi": AgentQualityGate(
            min_signal_confidence=0.55,
            max_daily_drawdown_pct=0.03,
            min_sample_size=30,
            min_win_rate=0.47,
            min_net_edge_pct=0.02,
        ),
        "polymarket_news": AgentQualityGate(
            min_signal_confidence=0.6,
            max_daily_drawdown_pct=0.025,
            min_sample_size=40,
            min_win_rate=0.5,
            min_net_edge_pct=0.03,
        ),
        "polymarket_copy": AgentQualityGate(
            min_signal_confidence=0.0,
            max_daily_drawdown_pct=0.025,
            min_sample_size=40,
            min_win_rate=0.5,
            min_net_edge_pct=0.02,
        ),
        "stocks_etf_paper_shell": AgentQualityGate(
            min_signal_confidence=0.0,
            max_daily_drawdown_pct=0.0,
            min_sample_size=0,
            min_win_rate=0.0,
            min_net_edge_pct=0.0,
        ),
    }

    engine = TradingEngine(
        exchange=primary,
        strategies=strategies,
        market_data=market_data,
        news=news,
        risk=risk,
        portfolio=portfolio,
        db=db,
        poll_interval=args.interval,
        day_trader_pipeline=day_trader_pipeline,
        polymarket_copy_agent=polymarket_copy_agent,
        stocks_etf_agent=stocks_etf_agent,
        agent_registry=agent_registry,
        exchange_map=exchange_map,
        quality_gates=quality_gates,
        approved_markets=approved_markets,
    )

    # Add any initial watchlist items
    if args.watch:
        for market_id in args.watch.split(","):
            market_data.add_to_watchlist(market_id.strip())

    # Build optional components
    tasks = []

    if args.api:
        import uvicorn
        from bot.api.server import app, set_engine
        set_engine(engine)

        config = uvicorn.Config(
            app,
            host=settings.API_HOST,
            port=settings.API_PORT,
            log_level="info",
        )
        server = uvicorn.Server(config)
        log.info(f"[bold]API server starting on http://{settings.API_HOST}:{settings.API_PORT}[/]")
        tasks.append(server.serve())

    if args.telegram:
        from bot.telegram_bot.bot import TelegramBot
        tg = TelegramBot(settings.TELEGRAM_BOT_TOKEN, engine, approved_markets=approved_markets)
        # Wire proposal callback: engine → telegram
        engine.on_market_proposed = tg.propose_market_to_user
        log.info("[bold]Telegram bot starting...[/]")
        tasks.append(tg.run())

    tasks.append(engine.start())

    await asyncio.gather(*tasks)


async def run_api_only():
    """Run only the API server (for dashboard development)."""
    import uvicorn
    from bot.api.server import app, set_database

    db = Database()
    await db.init()
    set_database(db)

    config = uvicorn.Config(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    log.info(f"[bold]API-only mode on http://{settings.API_HOST}:{settings.API_PORT}[/]")
    await server.serve()


def main():
    parser = argparse.ArgumentParser(
        description="MaxTheTrader - Automated prediction market trading bot"
    )
    parser.add_argument(
        "--exchange",
        default="kalshi",
        help="Exchange to trade on: kalshi, polymarket, or both (comma-separated). Default: kalshi",
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Trading mode. Default: paper",
    )
    parser.add_argument(
        "--strategy",
        default="all",
        help="Strategies to run: news, momentum, or all (comma-separated). Default: all",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Poll interval in seconds. Default: 30",
    )
    parser.add_argument(
        "--watch",
        default="",
        help="Comma-separated market IDs to add to watchlist",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Start the API server alongside the trading engine",
    )
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Run only the API server (for dashboard development)",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Start Telegram bot alongside trading engine",
    )
    parser.add_argument(
        "--day-trader",
        action="store_true",
        help="Enable the day trader pipeline (BTC 15-min + other configured markets)",
    )

    args = parser.parse_args()

    try:
        if args.api_only:
            asyncio.run(run_api_only())
        else:
            asyncio.run(run_bot(args))
    except KeyboardInterrupt:
        log.info("[bold]Shutting down...[/]")
        sys.exit(0)


if __name__ == "__main__":
    main()
