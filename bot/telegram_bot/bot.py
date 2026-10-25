"""Telegram bot — command interface and social media content pipeline for MaxTheTrader."""
from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

from bot.agents.knowledge_learner import KnowledgeLearnerAgent
from bot.agents.pipeline import AgentPipeline
from bot.agents.researcher import ResearcherAgent
from bot.agents.synthesizer import SynthesizerAgent
from bot.agents.trader import TraderAgent
from bot.config import settings
from bot.telegram_bot.content_fetcher import fetch_social_content
from bot.telegram_bot.tag_parser import parse_tags, URL_ONLY_SERIES
from bot.utils.logger import log


def _escape(text: str) -> str:
    """Escape special characters for MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def _fmt_usd(n: float) -> str:
    return f"${n:,.2f}"


def _fmt_pct(n: float) -> str:
    return f"{n*100:.1f}%"


class TelegramBot:
    """
    Telegram bot interface for MaxTheTrader.

    Runs concurrently alongside the trading engine using non-blocking
    Application.start() + start_polling() pattern.
    """

    def __init__(self, token: str, engine, approved_markets=None):
        self.token = token
        self.engine = engine
        self._approved_markets = approved_markets  # ApprovedMarketsStore | None
        self._app: Application | None = None
        self._allowed_user_ids = set(settings.TELEGRAM_ALLOWED_USER_IDS)

        # Build the agent pipeline using engine's components
        researcher = ResearcherAgent(
            news_collector=engine.news,
            market_fetcher=engine.market_data,
        )
        synthesizer = SynthesizerAgent()
        trader = TraderAgent(exchange=engine.exchange)

        # Initialize swarm layer if enabled
        swarm_layer = None
        if settings.SWARM_ENABLED and settings.ZEP_API_KEY:
            try:
                from bot.swarm.analysis_layer import SwarmAnalysisLayer
                swarm_layer = SwarmAnalysisLayer()
                log.info("[bold cyan]Swarm analysis layer initialized for pipeline[/]")
            except Exception as e:
                log.warning(f"Could not initialize swarm layer: {e}")

        self._pipeline = AgentPipeline(
            researcher=researcher,
            synthesizer=synthesizer,
            trader=trader,
            swarm_layer=swarm_layer,
        )

        # Knowledge learner agent (used for #learn YouTube URLs and Reddit posts)
        self._knowledge_learner = KnowledgeLearnerAgent()

        # Tracks subreddits already proposed this session — avoids re-proposing
        self._proposed_subreddits: set[str] = set()

    def _is_authorized(self, update: Update) -> bool:
        """Check if the sender is in the allowed user IDs list."""
        if not self._allowed_user_ids:
            # No restriction configured — allow all (warn in logs)
            log.warning("[yellow]TELEGRAM[/] No TELEGRAM_ALLOWED_USER_IDS set — allowing all users!")
            return True
        user_id = update.effective_user.id if update.effective_user else None
        return user_id in self._allowed_user_ids

    async def run(self):
        """Start the Telegram bot (non-blocking, runs alongside asyncio event loop)."""
        log.info("[bold green]Starting Telegram bot...[/]")
        self._app = Application.builder().token(self.token).build()
        self._register_handlers()

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        log.info("[bold green]Telegram bot running![/] Send /start to begin.")

        # Keep running until engine stops
        try:
            while self.engine._running:
                await asyncio.sleep(1)
        finally:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            log.info("Telegram bot stopped.")

    def _register_handlers(self):
        app = self._app

        # Commands
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("portfolio", self._cmd_portfolio))
        app.add_handler(CommandHandler("positions", self._cmd_positions))
        app.add_handler(CommandHandler("signals", self._cmd_signals))
        app.add_handler(CommandHandler("trades", self._cmd_trades))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))
        app.add_handler(CommandHandler("knowledge", self._cmd_knowledge))
        app.add_handler(CommandHandler("watchlist", self._cmd_watchlist))

        # Swarm Intelligence commands
        app.add_handler(CommandHandler("simulate", self._cmd_simulate))
        app.add_handler(CommandHandler("simstatus", self._cmd_simstatus))
        app.add_handler(CommandHandler("graph", self._cmd_graph))
        app.add_handler(CommandHandler("graphsearch", self._cmd_graphsearch))
        app.add_handler(CommandHandler("deepanalysis", self._cmd_deepanalysis))

        # Phase 6: Auto-Triggers & Learning Loop commands
        app.add_handler(CommandHandler("accuracy", self._cmd_accuracy))
        app.add_handler(CommandHandler("weights", self._cmd_weights))
        app.add_handler(CommandHandler("dailysim", self._cmd_dailysim))

        # Inline button callbacks (market approval / removal)
        app.add_handler(CallbackQueryHandler(
            self._cb_watchlist_approve, pattern=r"^wl_approve:"
        ))
        app.add_handler(CallbackQueryHandler(
            self._cb_watchlist_decline, pattern=r"^wl_decline:"
        ))
        app.add_handler(CallbackQueryHandler(
            self._cb_watchlist_remove, pattern=r"^wl_remove:"
        ))

        # URL messages — extract content and run pipeline
        app.add_handler(MessageHandler(
            filters.Entity("url") & filters.TEXT,
            self._handle_url,
        ))

        # Free text messages — run through pipeline directly
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_text,
        ))

    # ── Swarm Intelligence commands ──────────────────────────────────────────

    async def _cmd_simulate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run a swarm simulation: /simulate <topic>"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features are disabled\\. Set `SWARM_ENABLED=true` in \\.env", parse_mode=ParseMode.MARKDOWN_V2)
            return

        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text(
                "Usage: `/simulate BTC breaks 100k after ETF inflows`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await update.message.reply_text(
            f"🌊 *Starting swarm simulation\\.\\.\\.*\n_{_escape(topic[:80])}_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        try:
            from bot.swarm.analysis_layer import SwarmAnalysisLayer
            layer = SwarmAnalysisLayer()
            summary = await layer.run_standalone_simulation(topic)
            # Send as plain text since simulation output has special chars
            await update.message.reply_text(f"🌊 Simulation Complete\n\n{summary}")
        except Exception as e:
            await update.message.reply_text(f"❌ Simulation failed: {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_simstatus(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check simulation status: /simstatus"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        try:
            from bot.swarm.simulation_manager import SimulationManager
            manager = SimulationManager()
            sims = manager.list_simulations()
            if not sims:
                await update.message.reply_text("No simulations found\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return

            lines = ["*Recent Simulations:*\n"]
            for sim in sims[-5:]:
                status = sim.get("status", "unknown")
                sim_id = sim.get("simulation_id", "?")[:16]
                actions = sim.get("total_actions", 0)
                lines.append(f"`{_escape(sim_id)}` \\| {_escape(status)} \\| {actions} actions")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_graph(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show knowledge graph stats: /graph"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        try:
            from bot.swarm.async_wrappers import SwarmGraphAsync
            swarm = SwarmGraphAsync()
            graph_id = await swarm.get_graph_id()
            if not graph_id:
                await update.message.reply_text("No knowledge graph created yet\\. Use `\\#learn` to ingest content first\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return

            stats = await swarm.get_graph_statistics(graph_id)
            entity_types = stats.get("entity_types", {})
            relation_types = stats.get("relation_types", {})

            lines = [
                f"🧠 *Knowledge Graph*\n",
                f"Graph ID: `{_escape(graph_id[:20])}`",
                f"Nodes: {stats.get('total_nodes', 0)}",
                f"Edges: {stats.get('total_edges', 0)}",
                f"Expired edges: {stats.get('expired_edges', 0)}",
            ]
            if entity_types:
                lines.append("\n*Entity types:*")
                for etype, count in sorted(entity_types.items(), key=lambda x: -x[1]):
                    lines.append(f"  {_escape(etype)}: {count}")
            if relation_types:
                lines.append("\n*Relationship types:*")
                for rtype, count in sorted(relation_types.items(), key=lambda x: -x[1])[:8]:
                    lines.append(f"  {_escape(rtype)}: {count}")

            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_graphsearch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search the knowledge graph: /graphsearch <query>"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: `/graphsearch RSI oversold BTC`", parse_mode=ParseMode.MARKDOWN_V2)
            return

        try:
            from bot.swarm.async_wrappers import SwarmGraphAsync
            swarm = SwarmGraphAsync()
            graph_id = await swarm.get_graph_id()
            if not graph_id:
                await update.message.reply_text("No knowledge graph available\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return

            result = await swarm.quick_search(graph_id, query, limit=8)
            if not result.facts:
                await update.message.reply_text(f"No results for: _{_escape(query)}_", parse_mode=ParseMode.MARKDOWN_V2)
                return

            lines = [f"🔍 *Graph search:* _{_escape(query)}_\n"]
            for i, fact in enumerate(result.facts[:8], 1):
                lines.append(f"{i}\\. {_escape(fact[:150])}")

            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_deepanalysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run deep analysis: /deepanalysis <topic>"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text("Usage: `/deepanalysis BTC momentum after FOMC`", parse_mode=ParseMode.MARKDOWN_V2)
            return

        await update.message.reply_text(
            f"🔬 *Running deep analysis\\.\\.\\.*\n_{_escape(topic[:80])}_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        try:
            from bot.swarm.deep_analysis import DeepAnalysisAgent
            agent = DeepAnalysisAgent()
            analysis = await agent.analyze(topic)
            reply = analysis.format_telegram()
            await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ Analysis failed: {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    # ── Phase 6: Auto-Triggers & Learning Loop commands ─────────────────────

    async def _cmd_accuracy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show prediction accuracy stats: /accuracy"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        try:
            from bot.swarm.accuracy_tracker import AccuracyTracker
            tracker = AccuracyTracker()
            await tracker.init()
            stats = await tracker.get_archetype_stats()

            if not stats:
                await update.message.reply_text(
                    "📊 No prediction data yet\\. Run simulations and wait for markets to resolve\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return

            lines = ["📊 *Archetype Prediction Accuracy*\n━━━━━━━━━━━━━━━━━━━━"]
            for s in stats:
                bar = "🟢" if s.accuracy >= 0.55 else ("🟡" if s.accuracy >= 0.45 else "🔴")
                lines.append(
                    f"\n{bar} *{_escape(s.archetype)}*\n"
                    f"  Accuracy: `{s.accuracy:.0%}` \\({s.correct_predictions}/{s.total_predictions}\\)\n"
                    f"  Avg confidence: `{s.avg_confidence:.0%}`"
                )
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_weights(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show/adjust archetype weights: /weights [adjust]"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        try:
            from bot.swarm.weight_adjuster import WeightAdjuster
            adjuster = WeightAdjuster()

            if context.args and context.args[0] == "adjust":
                changes = await adjuster.apply_adjustments()
                if changes:
                    lines = ["⚖️ *Weight Adjustments Applied*\n━━━━━━━━━━━━━━━━━━━━"]
                    for c in changes:
                        lines.append(f"\n{_escape(c)}")
                    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await update.message.reply_text(
                        "⚖️ No adjustments needed \\(insufficient data or no changes\\)\\.",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                return

            weights = await adjuster.get_current_weights()
            lines = ["⚖️ *Archetype Weights*\n━━━━━━━━━━━━━━━━━━━━"]
            for arch, w in sorted(weights.items(), key=lambda x: -x[1]["current_weight"]):
                name = w.get("name", arch)
                delta = w["current_weight"] - w["baseline_weight"]
                delta_str = f" \\({'+' if delta > 0 else ''}{delta:.3f}\\)" if abs(delta) > 0.001 else ""
                acc_str = f" \\| acc: `{w['accuracy']:.0%}`" if w["accuracy"] >= 0 else ""
                lines.append(
                    f"\n*{_escape(name)}*: `{w['current_weight']:.2f}`{delta_str}{acc_str}"
                )
            lines.append(f"\n_Use `/weights adjust` to apply accuracy\\-based adjustments_")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_dailysim(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show daily simulation status or force run: /dailysim [run]"""
        if not await self._guard(update):
            return
        if not settings.SWARM_ENABLED:
            await update.message.reply_text("⚠️ Swarm features disabled\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        try:
            from bot.swarm.scheduled_simulations import ScheduledSimulationRunner
            runner = ScheduledSimulationRunner(self.engine.market_data)

            if context.args and context.args[0] == "run":
                await update.message.reply_text(
                    "🗓 *Running daily simulations\\.\\.\\.*",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                # Force run by resetting state
                runner._state.last_run_date = ""
                results = await runner.maybe_run_daily()
                if results:
                    lines = ["🗓 *Daily Simulations Complete*\n━━━━━━━━━━━━━━━━━━━━"]
                    for r in results:
                        emoji = "🟢" if r.consensus == "bullish" else ("🔴" if r.consensus == "bearish" else "🟡")
                        lines.append(
                            f"\n{emoji} *{_escape(r.market_title[:50])}*\n"
                            f"  Consensus: {_escape(r.consensus)} "
                            f"\\({r.bullish_count}B/{r.bearish_count}Br\\)\n"
                            f"  Actions: {r.total_actions}"
                        )
                    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await update.message.reply_text(
                        "🗓 No candidate markets found for daily simulation\\.",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                return

            # Show status
            stats = runner.get_stats()
            today_results = runner.get_today_results()
            lines = [
                "🗓 *Daily Simulation Status*\n━━━━━━━━━━━━━━━━━━━━",
                f"\nScheduled hour: {stats['sim_hour_utc']}:00 UTC",
                f"Max per day: {stats['max_sims_per_day']}",
                f"Total runs: {stats['total_runs']}",
                f"Last run: {_escape(stats['last_run_date'] or 'never')}",
            ]
            if today_results:
                lines.append(f"\n*Today's simulations:* {len(today_results)}")
                for r in today_results:
                    lines.append(f"  • {_escape(r.get('market_title', '?')[:40])} — {_escape(r.get('consensus', '?'))}")
            else:
                lines.append("\nNo simulations today yet\\.")
            lines.append(f"\n_Use `/dailysim run` to force run now_")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e)[:200])}", parse_mode=ParseMode.MARKDOWN_V2)

    # ── Content classifier ──────────────────────────────────────────────────

    async def _auto_classify(self, content: str, source_platform: str) -> str:
        """
        Classify content as 'knowledge' or 'signal' using a minimal Claude call.

        knowledge — educational/strategic content worth filing into the KB:
            trading strategies, how-to guides, technical analysis theory,
            backtesting results, indicator explanations, platform tutorials,
            Reddit posts discussing strategy, YouTube trading education.

        signal   — time-sensitive market content worth running through the
            research pipeline for immediate trade ideas:
            breaking news, specific price predictions, "BTC is about to…"
            commentary, macro events, earnings, Fed announcements.

        Cost: ~150 input tokens + 1 output token per call. Negligible.
        """
        import anthropic
        from bot.config import settings

        sample = content[:600].strip()
        prompt = (
            f"Source: {source_platform}\n\n"
            f"Content:\n{sample}\n\n"
            "Classify this trading content. Reply with exactly one word.\n"
            "KNOWLEDGE — if it teaches strategy, explains how to trade, "
            "or would be useful as a stored trading rule.\n"
            "SIGNAL — if it describes something happening right now that "
            "could move a prediction market in the next hours/days."
        )

        try:
            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            message = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=5,
                system=(
                    "You are a routing classifier for a prediction market trading bot. "
                    "Reply with exactly one word: KNOWLEDGE or SIGNAL."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            word = message.content[0].text.strip().upper()
            result = "knowledge" if "KNOWLEDGE" in word else "signal"
            log.info(f"[dim]Auto-classify ({source_platform}): {result}[/]")
            return result
        except Exception as e:
            log.warning(f"Auto-classify failed ({e}), defaulting to signal")
            return "signal"

    # ── Authorization guard ─────────────────────────────────────────────────

    async def _guard(self, update: Update) -> bool:
        """Returns True if authorized, silently returns False otherwise."""
        if not self._is_authorized(update):
            log.warning(
                f"[red]TELEGRAM[/] Unauthorized access attempt from user "
                f"{update.effective_user.id if update.effective_user else 'unknown'}"
            )
            return False
        return True

    def _resolve_knowledge_target_hint(self, tags, default: str = "auto") -> str:
        """
        Convert tags into an explicit knowledge target hint when possible.

        Returns an agent id, legacy track hint, or "auto".
        """
        if tags.intent == "kalshi":
            return "btc15m_kalshi"
        if tags.intent == "traditional":
            return "stocks_etf_paper_shell"

        raw = set(tags.raw_tags or [])
        if "btc15m" in raw or "kxbtc15m" in raw or "btc15" in raw:
            return "btc15m_kalshi"
        if "polymarket" in raw:
            return "polymarket_news"
        if "stocks" in raw or "etf" in raw or "equities" in raw:
            return "stocks_etf_paper_shell"
        return default

    # ── Command Handlers ────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        text = (
            "👋 *MaxTheTrader Bot*\n\n"
            "I'm your prediction market trading assistant\\. "
            "Send me social media posts, news URLs, or YouTube videos and I'll analyze them\\.\n\n"
            "*Commands:*\n"
            "/status \\— Bot status \\& portfolio summary\n"
            "/portfolio \\— Full portfolio view\n"
            "/positions \\— Active positions\n"
            "/signals \\— Recent trading signals\n"
            "/trades \\— Trade history\n"
            "/knowledge \\— View accumulated trading knowledge\n"
            "/watchlist \\— View \\& manage monitored markets\n"
            "/pause \\— Pause automated trading\n"
            "/resume \\— Resume trading\n\n"
            "*Swarm Intelligence:*\n"
            "/simulate \\<topic\\> \\— Run swarm simulation\n"
            "/simstatus \\— Check simulation status\n"
            "/graph \\— Knowledge graph stats\n"
            "/graphsearch \\<query\\> \\— Search the graph\n"
            "/deepanalysis \\<topic\\> \\— Deep ReACT analysis\n\n"
            "*Learning Loop \\(Phase 6\\):*\n"
            "/accuracy \\— Archetype prediction accuracy\n"
            "/weights \\[adjust\\] \\— View/adjust archetype weights\n"
            "/dailysim \\[run\\] \\— Daily simulation status/trigger\n\n"
            "*Send me a URL with a tag:*\n"
            "`\\#learn` \\— Extract \\& file trading knowledge from a YouTube video\n"
            "`\\#news` \\— Analyze for real\\-time trading signals\n"
            "`\\#btc15m` \\— Target the BTC 15\\-minute market\n"
            "`\\#swarm` \\— Force swarm simulation on content\n\n"
            "*Examples:*\n"
            "`\\#learn https://youtube\\.com/watch\\?v\\=\\.\\.\\.`\n"
            "`\\#news \\#btc15m https://x\\.com/\\.\\.\\.`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._cmd_start(update, context)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        try:
            state = await self.engine.portfolio.get_state(self.engine.exchange)
            paused = not self.engine._running
            mode_tag = "PAPER 📋" if settings.PAPER_MODE else "LIVE 🔴"
            pnl_arrow = "▲" if state.unrealized_pnl >= 0 else "▼"
            signals_count = len(self.engine._signals)
            cycle = self.engine._cycle_count

            text = (
                f"📊 *MaxTheTrader Status*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{'⏸ PAUSED' if paused else '🟢 Running'} \\| Cycle \\#{_escape(str(cycle))}\n"
                f"Mode: {_escape(mode_tag)}\n\n"
                f"💰 Balance: `{_escape(_fmt_usd(state.balance))}`\n"
                f"📈 Total Value: `{_escape(_fmt_usd(state.total_value))}`\n"
                f"⚡ P\\&L: `{pnl_arrow} {_escape(_fmt_usd(abs(state.unrealized_pnl)))}`\n"
                f"📂 Positions: {state.num_positions}\n"
                f"🎯 Signals queued: {signals_count}\n"
                f"\n_Last updated: {_escape(datetime.utcnow().strftime('%H:%M:%S UTC'))}_"
            )
        except Exception as e:
            text = f"❌ Error fetching status: {_escape(str(e))}"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        try:
            state = await self.engine.portfolio.get_state(self.engine.exchange)
            realized = state.realized_pnl
            r_arrow = "▲" if realized >= 0 else "▼"
            u_arrow = "▲" if state.unrealized_pnl >= 0 else "▼"

            text = (
                f"💼 *Portfolio*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Balance: `{_escape(_fmt_usd(state.balance))}`\n"
                f"Total Value: `{_escape(_fmt_usd(state.total_value))}`\n"
                f"Exposure: `{_escape(_fmt_usd(state.total_exposure))}`\n\n"
                f"Unrealized P\\&L: `{u_arrow} {_escape(_fmt_usd(abs(state.unrealized_pnl)))}`\n"
                f"Realized P\\&L: `{r_arrow} {_escape(_fmt_usd(abs(realized)))}`\n"
                f"\nPositions: {state.num_positions}"
            )
        except Exception as e:
            text = f"❌ {_escape(str(e))}"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        try:
            positions = await self.engine.exchange.get_positions()
            if not positions:
                await update.message.reply_text("📂 No open positions\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return

            lines = ["📂 *Active Positions*\n━━━━━━━━━━━━━━━━━━━━"]
            for p in positions:
                pnl_arrow = "▲" if p.unrealized_pnl >= 0 else "▼"
                lines.append(
                    f"\n*{_escape(p.market_title[:45] or p.market_id)}*\n"
                    f"  {_escape(p.outcome)} \\| Size: `{_escape(f'{p.size:.1f}')}`\n"
                    f"  Entry: `{_escape(_fmt_pct(p.avg_entry_price))}` → "
                    f"Current: `{_escape(_fmt_pct(p.current_price))}`\n"
                    f"  P\\&L: `{pnl_arrow} {_escape(_fmt_usd(abs(p.unrealized_pnl)))}`"
                )
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        recent = self.engine.get_recent_signals(limit=5)
        if not recent:
            await update.message.reply_text("🎯 No recent signals\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        lines = ["🎯 *Recent Signals*\n━━━━━━━━━━━━━━━━━━━━"]
        for s in recent:
            action_emoji = "🟢" if s["action"] == "buy" else "🔴"
            lines.append(
                f"\n{action_emoji} *{_escape(s['action'].upper())} {_escape(s['outcome'])}*\n"
                f"  {_escape(s.get('market_title', s['market_id'])[:45])}\n"
                f"  Strategy: `{_escape(s['strategy'])}` \\| Conf: `{int(s['confidence']*100)}%`\n"
                f"  _{_escape(s['reasoning'][:80])}_"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        try:
            trades = await self.engine.db.get_trades(limit=10)
            if not trades:
                await update.message.reply_text("📋 No trades yet\\.", parse_mode=ParseMode.MARKDOWN_V2)
                return
            lines = ["📋 *Recent Trades*\n━━━━━━━━━━━━━━━━━━━━"]
            for t in trades[:8]:
                side_emoji = "🟢" if t["side"] == "buy" else "🔴"
                pnl = t.get("pnl", 0) or 0
                pnl_str = f"P\\&L: `{_escape(_fmt_usd(pnl))}`" if pnl else ""
                size_str = f"{t['size']:.1f}"
                lines.append(
                    f"\n{side_emoji} `{t['side'].upper()}` {_escape(t['outcome'])} @ "
                    f"`{_escape(_fmt_pct(t['price']))}` sz={_escape(size_str)} "
                    f"{pnl_str}"
                )
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {_escape(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        self.engine._running = False
        await update.message.reply_text(
            "⏸ *Trading paused*\\. Send /resume to restart\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        self.engine._running = True
        asyncio.create_task(self.engine._trading_loop())
        await update.message.reply_text(
            "▶️ *Trading resumed*\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def _cmd_knowledge(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show a per-agent summary of accumulated knowledge."""
        if not await self._guard(update):
            return
        try:
            from bot.knowledge import knowledge_base as kb
            from bot.knowledge.categories import AGENT_KNOWLEDGE_PROFILES

            lines = ["📚 *Accumulated Trading Knowledge \\(Per Agent\\)*\n━━━━━━━━━━━━━━━━━━━━"]
            grand_rules = 0
            grand_sources = 0
            for agent_id, profile in AGENT_KNOWLEDGE_PROFILES.items():
                summary = kb.summarize_agent_knowledge(agent_id)
                label = profile.get("label", agent_id)
                grand_rules += summary["total_rules"]
                grand_sources += summary["sources"]
                lines.append(
                    f"\n🤖 *{_escape(label)}*\n"
                    f"  Agent: `{_escape(agent_id)}`\n"
                    f"  Track: {_escape(summary['track'])}\n"
                    f"  Rules: {summary['total_rules']} \\| Sources: {summary['sources']}"
                    + (f" \\| ⚡ {summary['contradictions']} conflicts" if summary["contradictions"] else "")
                )

                top_categories = sorted(summary["categories"], key=lambda c: c["rules"], reverse=True)[:2]
                for c in top_categories:
                    if c["rules"] == 0:
                        continue
                    lines.append(
                        f"    • {_escape(c['category'].replace('_', ' '))}: {c['rules']} "
                        f"\\(✅ {c['confirmed']} \\| 🔸 {c['tentative']}\\)"
                    )

            lines.append(
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {grand_rules} total rules across agents \\| 🎥 {grand_sources} processed sources\n"
                f"Use tags like `\\#btc15m`, `\\#polymarket`, or `\\#stocks` with `\\#learn` "
                f"to force an agent target\\."
            )
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)

        except Exception as e:
            await update.message.reply_text(
                f"❌ {_escape(str(e))}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

    async def _cmd_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show all monitored markets with inline remove buttons."""
        if not await self._guard(update):
            return

        store = self._approved_markets
        always = store.always_approved_series if store else {}
        approved = store.get_all_approved() if store else []

        lines = ["📋 *Monitored Markets*\n━━━━━━━━━━━━━━━━━━━━"]

        # Always-on series (pre-approved, can't be removed)
        if always:
            lines.append("\n*Pre\\-approved series* \\(always active\\):")
            for ticker, display in always.items():
                active_id = self.engine.market_data.get_series_market_id(ticker)
                active_str = f"\n  `{_escape(active_id)}`" if active_id else " \\(not yet seeded\\)"
                lines.append(f"  🔵 *{_escape(display)}*{active_str}")

        # User-approved markets
        if approved:
            lines.append("\n*User\\-approved markets:*")
            for m in approved:
                vol_str = f"${m.volume:,.0f}" if m.volume else "n/a"
                lines.append(
                    f"\n  ✅ _{_escape(m.title[:50])}_\n"
                    f"  `{_escape(m.market_id)}` \\| vol {_escape(vol_str)}"
                )
        elif not always:
            lines.append("\n_No markets being monitored yet\\._")
            lines.append("_Auto\\-discovery will propose new markets when found\\._")

        lines.append(
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"_{len(approved)} user\\-approved \\| {len(always)} pre\\-approved series_"
        )

        # Build inline keyboard: one Remove button per user-approved market
        keyboard = []
        for m in approved:
            label = m.title[:30] if m.title else m.market_id
            keyboard.append([InlineKeyboardButton(
                f"🗑 Remove: {label[:28]}",
                callback_data=f"wl_remove:{m.market_id}",
            )])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )

    # ── Watchlist inline callbacks ──────────────────────────────────────────

    async def _cb_watchlist_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User tapped ✅ on a market proposal — approve and add to watchlist."""
        query = update.callback_query
        await query.answer()

        if not self._is_authorized(update):
            return

        # callback_data format: "wl_approve:{market_id}:{series_ticker}:{title_b64}"
        parts = query.data.split(":", 3)
        if len(parts) < 4:
            await query.edit_message_text("❌ Invalid callback data.")
            return

        _, market_id, series_ticker, title = parts
        import base64
        title = base64.b64decode(title.encode()).decode(errors="replace")

        if self._approved_markets:
            from bot.data.approved_markets import ApprovedMarket
            from datetime import datetime, timezone
            self._approved_markets.approve(ApprovedMarket(
                market_id=market_id,
                series_ticker=series_ticker,
                title=title,
                exchange="kalshi",
                volume=0.0,
                approved_at=datetime.now(timezone.utc).isoformat(),
            ))
            # Add to live watchlist immediately
            self.engine.market_data.add_to_watchlist(market_id)

        await query.edit_message_text(
            f"✅ *Added to watchlist*\n`{_escape(market_id)}`\n_{_escape(title[:60])}_",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        log.info(f"[WATCHLIST] User approved via Telegram: {market_id}")

    async def _cb_watchlist_decline(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User tapped ❌ on a market proposal — decline and don't ask again."""
        query = update.callback_query
        await query.answer()

        if not self._is_authorized(update):
            return

        parts = query.data.split(":", 2)
        market_id = parts[1] if len(parts) > 1 else ""

        if self._approved_markets and market_id:
            self._approved_markets.decline(market_id)

        await query.edit_message_text(
            f"❌ *Skipped* — won't propose again\n`{_escape(market_id)}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def _cb_watchlist_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User tapped 🗑 Remove in /watchlist — remove from approved list."""
        query = update.callback_query
        await query.answer()

        if not self._is_authorized(update):
            return

        parts = query.data.split(":", 2)
        market_id = parts[1] if len(parts) > 1 else ""

        removed = None
        if self._approved_markets and market_id:
            removed = self._approved_markets.remove(market_id)
            self.engine.market_data.remove_from_watchlist(market_id)

        if removed:
            await query.edit_message_text(
                f"🗑 *Removed from watchlist*\n`{_escape(market_id)}`\n"
                f"_{_escape(removed.title[:60])}_",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            await query.edit_message_text(
                f"⚠️ Market not found in watchlist: `{_escape(market_id)}`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

    # ── Proposal sender (called by engine's on_market_proposed callback) ────

    async def propose_market_to_user(self, market) -> None:
        """
        Send a market proposal to all allowed users via Telegram.

        Called by the engine when auto-discovery finds a new market that hasn't
        been approved or declined yet. The user taps ✅ or ❌ inline buttons.

        market: bot.exchanges.base.Market
        """
        if not self._app or not self._allowed_user_ids:
            return

        import base64
        title_b64 = base64.b64encode(market.title.encode()).decode()
        series = market.category or ""

        # Truncate callback_data to Telegram's 64-byte limit per field
        # Format: wl_approve:{market_id}:{series}:{title_b64}
        # market_id ~40 chars, series ~10 chars, title_b64 can be long — cap it
        title_b64 = title_b64[:20]  # enough to recover ~15 chars of title

        approve_cb = f"wl_approve:{market.id}:{series}:{title_b64}"
        decline_cb = f"wl_decline:{market.id}"

        vol_str = f"${market.volume:,.0f}" if market.volume else "n/a"
        closes_str = ""
        if market.end_date:
            from datetime import timezone
            now = __import__("datetime").datetime.now(timezone.utc)
            end = market.end_date
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            hours_left = (end - now).total_seconds() / 3600
            closes_str = f"\nCloses in: {hours_left:.1f}h"

        text = (
            f"📊 *New Market Found*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_{_escape(market.title[:70])}_\n"
            f"`{_escape(market.id)}`\n"
            f"Volume: {_escape(vol_str)}"
            f"{_escape(closes_str)}\n\n"
            f"Add to watchlist?"
        )

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅  Monitor", callback_data=approve_cb),
            InlineKeyboardButton("❌  Skip", callback_data=decline_cb),
        ]])

        for user_id in self._allowed_user_ids:
            try:
                await self._app.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
            except Exception as e:
                log.warning(f"[WATCHLIST] Could not send proposal to {user_id}: {e}")

    # ── Message Handlers ────────────────────────────────────────────────────

    async def _handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages containing URLs — fetch content, auto-classify, then route."""
        if not await self._guard(update):
            return

        raw_message = update.message.text or ""
        tags = parse_tags(raw_message)

        # Extract URL from message entities
        entities = update.message.entities or []
        url = None
        for entity in entities:
            if entity.type == "url":
                url = raw_message[entity.offset: entity.offset + entity.length]
                break

        if not url:
            await update.message.reply_text("❓ No URL found in message\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return

        # Explicit #learn / #kalshi overrides — skip classifier, go straight to KB
        if tags.intent == "learn":
            await self._handle_learn_url(
                update,
                url,
                force_track=self._resolve_knowledge_target_hint(tags, default="auto"),
            )
            return
        if tags.intent == "kalshi":
            await self._handle_learn_url(
                update,
                url,
                force_track=self._resolve_knowledge_target_hint(tags, default="btc15m_kalshi"),
            )
            return

        await update.message.reply_text(
            f"🔗 *Fetching content\\.\\.\\.*\n`{_escape(url[:60])}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        # ── Fetch content ────────────────────────────────────────────────────
        fetched = await fetch_social_content(url)
        platform = fetched.source_platform

        # For short-form video platforms, report YouTube transcript result
        _video_platforms = {"TikTok", "Instagram", "Facebook"}
        if platform in _video_platforms:
            if fetched.transcript:
                yt_line = (
                    f"📺 *Found on YouTube:* _{_escape(fetched.video_title[:55])}_"
                    + (f" by {_escape(fetched.channel_name)}" if fetched.channel_name else "")
                    + f"\nTranscript extracted \\({len(fetched.transcript):,} chars\\)"
                )
                await update.message.reply_text(yt_line, parse_mode=ParseMode.MARKDOWN_V2)
            elif fetched.found_on_youtube:
                await update.message.reply_text(
                    f"📺 Found on YouTube but no transcript\\: _{_escape(fetched.video_title[:55])}_\n"
                    f"Analysing caption only\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(
                    f"⚠️ *{_escape(platform)} video not found on YouTube\\.* Using caption only\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

        if not fetched.success and not fetched.transcript:
            error_detail = _escape(fetched.error[:120]) if fetched.error else ""
            detail_line  = f"\n_Reason: {error_detail}_" if error_detail else ""
            await update.message.reply_text(
                f"⚠️ Could not extract content from {_escape(platform)} URL\\."
                f"{detail_line}\n"
                f"Try pasting the post text directly instead\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # ── Follow embedded YouTube links in post body ─────────────────────
        embedded_transcript = ""
        if fetched.embedded_urls:
            try:
                from bot.telegram_bot.video_fetcher import (
                    _is_youtube_url, _extract_youtube_id, _get_transcript_for_id,
                )
                for emb_url in fetched.embedded_urls:
                    if _is_youtube_url(emb_url):
                        yt_id = _extract_youtube_id(emb_url)
                        if yt_id:
                            log.info(f"[LINK FOLLOW] Extracting transcript from embedded YouTube: {emb_url[:60]}")
                            await update.message.reply_text(
                                f"📺 *Post contains a YouTube link — extracting transcript\\.\\.\\.*\n"
                                f"`{_escape(emb_url[:60])}`",
                                parse_mode=ParseMode.MARKDOWN_V2,
                            )
                            result = await _get_transcript_for_id(yt_id)
                            if result.transcript:
                                embedded_transcript = result.transcript
                                log.info(f"[LINK FOLLOW] Transcript extracted: {len(embedded_transcript):,} chars")
                                await update.message.reply_text(
                                    f"✅ Transcript extracted \\({len(embedded_transcript):,} chars\\)",
                                    parse_mode=ParseMode.MARKDOWN_V2,
                                )
                            else:
                                reason = result.transcript_error or "unknown"
                                log.warning(f"[LINK FOLLOW] No transcript available: {reason}")
                                await update.message.reply_text(
                                    f"⚠️ YouTube transcript unavailable — processing post text only\\.",
                                    parse_mode=ParseMode.MARKDOWN_V2,
                                )
                        break  # only follow the first YouTube link
            except Exception as e:
                log.warning(f"[LINK FOLLOW] Failed to process embedded URLs: {e}")
                # Continue with Reddit text only — don't let link-following kill the handler

        # Assemble pipeline content — transcript beats caption
        if fetched.transcript:
            pipeline_content = (
                f"[VIDEO TRANSCRIPT — {fetched.video_title or platform}]\n"
                f"{fetched.transcript}"
            )
            if fetched.text:
                pipeline_content += f"\n\n[POST CAPTION]\n{fetched.text}"
            preview_label = "Transcript"
        elif embedded_transcript:
            pipeline_content = (
                f"[REDDIT POST]\n{fetched.text}\n\n"
                f"[EMBEDDED VIDEO TRANSCRIPT]\n{embedded_transcript}"
            )
            preview_label = f"{platform} + YouTube transcript"
        else:
            pipeline_content = fetched.text or ""
            preview_label = platform

        if not pipeline_content:
            await update.message.reply_text(
                f"⚠️ No content extracted from {_escape(platform)} URL\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # ── Classify — unless #news was explicitly set ───────────────────────
        if tags.intent == "news":
            route = "signal"
        else:
            route = await self._auto_classify(pipeline_content, platform)

        route_emoji = "📚" if route == "knowledge" else "📰"
        route_label = "Filing as trading knowledge" if route == "knowledge" else "Analysing for signals"
        preview = pipeline_content[:200]

        ack = (
            f"📄 *{_escape(preview_label)} preview:*\n_{_escape(preview)}_\n\n"
            f"{route_emoji} *{_escape(route_label)}\\.\\.\\.*"
        )
        if tags.has_market_tags:
            ack += f"\n{tags.confirmation_line()}"
        await update.message.reply_text(ack, parse_mode=ParseMode.MARKDOWN_V2)

        # ── Route ────────────────────────────────────────────────────────────
        if route == "knowledge":
            # For Reddit posts, use subreddit name as track signal rather than
            # relying solely on content keyword scoring.
            force_track = self._resolve_knowledge_target_hint(tags, default="auto")
            subreddit_hint = getattr(fetched, "subreddit", "")
            # Use canonical URL for dedup — Reddit share links (/s/XXXX)
            # resolve to the same /comments/{id}/ canonical URL.
            source_url = fetched.canonical_url or url
            await self._handle_learn_content(
                update, pipeline_content, source_url, platform,
                force_track=force_track, subreddit=subreddit_hint,
            )
            # After filing any post, check if the source is worth batch scraping
            await self._maybe_propose_source(update, fetched)
        else:
            pinned = await self._resolve_pinned_markets(tags.series_tickers)
            force_swarm = tags.intent == "swarm" or "swarm" in (tags.search_hints or [])
            await self._run_pipeline(
                update, pipeline_content, platform,
                pinned_markets=pinned, search_hints=tags.search_hints,
                force_swarm=force_swarm,
            )

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle free-text messages — auto-classify then route to KB or signal pipeline."""
        if not await self._guard(update):
            return

        raw_message = update.message.text.strip()
        tags = parse_tags(raw_message)
        content = tags.clean_text

        if len(content) < 10 and not tags.has_tags:
            await update.message.reply_text(
                "💬 Send me a Reddit post, trading insight, or news summary to analyse\\.\n"
                "Tip: add *\\#btc15m* to pin analysis to the BTC 15\\-min market\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # Block plain text for URL-only series (15-min markets resolve too fast)
        blocked = [t for t in tags.series_tickers if t in URL_ONLY_SERIES]
        if blocked:
            from bot.telegram_bot.tag_parser import SERIES_DISPLAY_NAMES
            names = ", ".join(SERIES_DISPLAY_NAMES.get(t, t) for t in blocked)
            await update.message.reply_text(
                f"⏱ *{_escape(names)}* resolves in 15 minutes \\— plain text is too slow\\.\n\n"
                f"Share a URL instead: `\\#btc15m https://x\\.com/your\\-post`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # Explicit intent overrides
        if tags.intent == "learn":
            await update.message.reply_text(
                "⚠️ `\\#learn` requires a YouTube URL to extract a transcript\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # Auto-classify unless #news was explicitly set
        if tags.intent == "news":
            route = "signal"
        else:
            route = await self._auto_classify(content, "text")

        route_emoji = "📚" if route == "knowledge" else "📰"
        route_label = "Filing as trading knowledge" if route == "knowledge" else "Analysing for signals"

        ack_lines = [f"💬 *Received\\.*  {route_emoji} {_escape(route_label)}\\.\\.\\.*"]
        if tags.has_market_tags:
            ack_lines.append(tags.confirmation_line())
        await update.message.reply_text("\n".join(ack_lines), parse_mode=ParseMode.MARKDOWN_V2)

        if route == "knowledge":
            await self._handle_learn_content(
                update,
                content,
                source_url="",
                platform="text",
                force_track=self._resolve_knowledge_target_hint(tags, default="auto"),
            )
        else:
            pinned = await self._resolve_pinned_markets(tags.series_tickers)
            force_swarm = tags.intent == "swarm" or "swarm" in (tags.search_hints or [])
            await self._run_pipeline(
                update, content, "text",
                pinned_markets=pinned, search_hints=tags.search_hints,
                force_swarm=force_swarm,
            )

    # ── Subreddit batch scraping ─────────────────────────────────────────────

    # ── Source-aware batch scraping (platform-generalized) ───────────────────

    async def _maybe_propose_source(
        self, update: Update, fetched
    ) -> None:
        """
        After ingesting any post, identify its source and propose batch scraping
        if the source is relevant and supported.

        Handles Reddit, YouTube, Instagram, TikTok, X/Twitter, LinkedIn.
        Only Reddit is fully scraped automatically; other platforms show an
        informational message with honest status about what's possible.
        """
        try:
            await self._maybe_propose_source_inner(update, fetched)
        except Exception as e:
            log.warning(f"[SOURCE PROPOSAL] Unexpected error: {e}", exc_info=True)

    async def _maybe_propose_source_inner(
        self, update: Update, fetched
    ) -> None:
        from bot.data.source_scraper import detect_source, fetch_source_top_posts

        platform = fetched.source_platform
        subreddit = getattr(fetched, "subreddit", "")

        # Build source info — pull whatever metadata we have from the fetched result
        source = detect_source(
            platform=platform,
            url=fetched.url,
            subreddit=subreddit,
            channel_name=getattr(fetched, "channel_name", ""),
            channel_url=getattr(fetched, "youtube_url", ""),
        )

        if source is None:
            log.debug(f"[SOURCE PROPOSAL] No relevant source detected for {platform} post")
            return

        # Avoid re-proposing the same source this session
        source_key = f"{source.platform}:{source.source_id}".lower()
        if source_key in self._proposed_subreddits:
            return
        self._proposed_subreddits.add(source_key)

        track_label = "Kalshi" if source.track == "kalshi" else "Traditional"

        # ── Platform not supported for batch scraping ────────────────────────
        if not source.scrape_supported:
            reason = _escape(source.unsupported_reason)
            await update.message.reply_text(
                f"📡 *Source identified: {_escape(source.source_name)}*\n"
                f"_Platform: {_escape(source.platform)} \\| Track: {_escape(track_label)}_\n"
                f"_Ranking by: {_escape(source.ranking_label)}_\n\n"
                f"⚠️ _{reason}_",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # ── Platform supported — fetch, score, and auto-ingest ───────────────
        log.info(f"[SOURCE AUTO-INGEST] Fetching candidates from {source.source_name} ({source.platform})")
        posts = await fetch_source_top_posts(source, limit=25, min_score=1)
        if not posts:
            log.warning(f"[SOURCE AUTO-INGEST] No posts returned for {source.source_name}")
            return

        # Score and filter by engagement quality
        from bot.data.source_scraper import score_and_filter_posts
        quality_posts = score_and_filter_posts(posts, source_name=source.source_name)
        if not quality_posts:
            log.info(f"[SOURCE AUTO-INGEST] {source.source_name}: no posts passed quality filter")
            return

        # Notify user (informational — no buttons needed)
        display = _escape(source.source_name)
        try:
            await update.message.reply_text(
                f"📡 *Auto\\-ingesting {len(quality_posts)} quality posts from {display}*\n"
                f"_Track: {_escape(track_label)} \\| Quality filter applied_",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception:
            pass

        # Fire autonomous ingest with the pre-filtered list
        import asyncio as _aio
        _aio.get_event_loop().create_task(
            self._run_source_ingest(
                update, source.platform, source.source_id, source.track,
                limit=len(quality_posts),
                pre_filtered_posts=quality_posts,
            )
        )

    async def _run_source_ingest(
        self,
        update: Update,
        platform: str,
        source_id: str,
        track: str,
        limit: int,
        *,
        pre_filtered_posts: list | None = None,
    ) -> None:
        """
        Batch-ingest posts from a source into the knowledge base.

        If pre_filtered_posts is provided (from autonomous quality scoring),
        those are used directly. Otherwise, fetches and uses raw top posts.

        Steps per post:
          1. Dedup check via kb.has_processed_video(url)
          2. Fetch full content (platform-specific)
          3. Run KnowledgeLearnerAgent.learn() with resolved track
          4. Accumulate stats; send progress updates
          5. Sleep 1.5s between posts to respect rate limits
        """
        from bot.data.source_scraper import SourceInfo, fetch_source_top_posts
        from bot.data.relevant_subreddits import is_relevant_subreddit
        from bot.knowledge import knowledge_base as kb
        from bot.knowledge.categories import detect_target_agent
        from bot.telegram_bot.content_fetcher import _fetch_reddit_post

        target_agent = detect_target_agent("", hint=track)

        # Reconstruct a minimal SourceInfo for the fetcher
        relevant, _ = is_relevant_subreddit(source_id) if platform == "Reddit" else (True, track)
        source = SourceInfo(
            platform=platform,
            source_id=source_id,
            source_name=f"r/{source_id}" if platform == "Reddit" else f"@{source_id}",
            source_url="",
            track=track,
            ranking_label="upvotes" if platform == "Reddit" else "score",
            scrape_supported=True,
        )

        if pre_filtered_posts is not None:
            posts = pre_filtered_posts
        else:
            posts = await fetch_source_top_posts(source, limit=limit, min_score=1)
        if not posts:
            display = source.source_name
            await update.message.reply_text(
                f"⚠️ No posts found for {_escape(display)}\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        total          = len(posts)
        new_rules      = 0
        confirmed      = 0
        contradictions = 0
        skipped        = 0
        processed      = 0

        for i, post in enumerate(posts, start=1):
            # Use canonical URL for dedup — batch posts from the subreddit
            # API already use canonical /comments/ URLs, so this also matches
            # posts previously ingested via share links.
            canonical = post.url
            if kb.has_processed_video(canonical, agent_id=target_agent):
                skipped += 1
                continue

            title_short = (post.title[:45] + "…") if len(post.title) > 45 else post.title
            try:
                await update.message.reply_text(
                    f"⚙️ *Processing {i}/{total}:* _{_escape(title_short)}_",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception:
                pass

            # Fetch full content (Reddit only for now)
            if platform == "Reddit":
                fetched = await _fetch_reddit_post(post.url)
                content = fetched.text if (fetched.success and fetched.text) else ""
                canonical = fetched.canonical_url or post.url

                # Follow embedded YouTube links for richer content
                if content and fetched.embedded_urls:
                    from bot.telegram_bot.video_fetcher import (
                        _is_youtube_url, _extract_youtube_id, _get_transcript_for_id,
                    )
                    for emb_url in fetched.embedded_urls:
                        if _is_youtube_url(emb_url):
                            yt_id = _extract_youtube_id(emb_url)
                            if yt_id:
                                log.info(f"[LINK FOLLOW] Batch: extracting transcript from {emb_url[:60]}")
                                result = await _get_transcript_for_id(yt_id)
                                if result.transcript:
                                    content = (
                                        f"[REDDIT POST]\n{content}\n\n"
                                        f"[EMBEDDED VIDEO TRANSCRIPT]\n{result.transcript}"
                                    )
                                    log.info(f"[LINK FOLLOW] Batch: +{len(result.transcript):,} chars from video")
                            break  # only first YouTube link
            else:
                content = ""

            if not content:
                skipped += 1
                await asyncio.sleep(1.5)
                continue

            try:
                report = await self._knowledge_learner.learn(
                    transcript=content,
                    video_title=post.title,
                    channel_name=source.source_name,
                    source_url=canonical,
                    track=track,
                )
                if not report.already_processed:
                    new_rules      += report.new_rules
                    confirmed      += report.confirmed_rules
                    contradictions += report.contradictions
                    processed      += 1
                else:
                    skipped += 1
            except Exception as e:
                log.warning(f"[SOURCE INGEST] learn() failed for {post.url}: {e}")
                skipped += 1

            await asyncio.sleep(1.5)

        track_label = "Kalshi KB" if track == "kalshi" else "Traditional KB"
        summary = (
            f"✅ *Source ingest complete: {_escape(source.source_name)}*\n"
            f"📥 {processed} posts processed \\({total} fetched\\)\n"
            f"🆕 {new_rules} new rules added to {_escape(track_label)}\n"
            f"✅ {confirmed} confirmations\n"
            f"⚡ {contradictions} contradictions flagged\n"
            f"⏭️ {skipped} posts skipped \\(already processed or empty\\)"
        )
        await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN_V2)

    # ── Knowledge base ingestion ─────────────────────────────────────────────

    async def _handle_learn_content(
        self,
        update: Update,
        content: str,
        source_url: str = "",
        platform: str = "text",
        force_track: str = "auto",
        subreddit: str = "",
    ):
        """
        Run the KnowledgeLearnerAgent on any already-extracted content string.

        Used by the auto-router for Reddit posts, plain text, and any fetched
        content classified as 'knowledge'. For YouTube URLs the dedicated
        _handle_learn_url path is preferred because it extracts transcripts.

        force_track: "auto" (detect) | track hint ("kalshi"/"traditional")
                    | explicit agent id ("btc15m_kalshi", "polymarket_news",
                    "stocks_etf_paper_shell")
        subreddit:   Subreddit name for Reddit posts — used as a strong track prior.
        """
        char_count = len(content)
        source_label = platform if platform != "text" else "text message"
        if force_track == "kalshi":
            track_hint = " \\(Kalshi track\\)"
        elif force_track in ("btc15m_kalshi", "polymarket_news", "stocks_etf_paper_shell"):
            track_hint = f" \\(target agent: {force_track}\\)"
        else:
            track_hint = ""

        await update.message.reply_text(
            f"📚 *Analysing {_escape(source_label)} for trading knowledge{track_hint}\\.*\n"
            f"_{char_count:,} chars — cross\\-referencing knowledge base\\.\\.\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        try:
            report = await self._knowledge_learner.learn(
                transcript=content,
                video_title=source_url or f"{platform} post",
                channel_name=platform,
                source_url=source_url or f"direct:{platform}",
                track=force_track,
                subreddit=subreddit,
            )
            await update.message.reply_text(
                report.format_telegram_reply(),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            log.error(f"Knowledge learner error: {e}")
            await update.message.reply_text(
                f"❌ Knowledge extraction failed: {_escape(str(e))}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

    async def _handle_learn_url(self, update: Update, url: str, force_track: str = "auto"):
        """
        Handle a #learn YouTube URL:
          1. Extract the transcript
          2. Run the KnowledgeLearnerAgent (cross-analyze vs knowledge base)
          3. Report what was learned
        """
        from bot.telegram_bot.video_fetcher import (
            _is_youtube_url, _extract_youtube_id, _get_transcript_for_id,
        )

        # Must be a YouTube URL for #learn
        if not _is_youtube_url(url):
            await update.message.reply_text(
                "⚠️ `\\#learn` only works with YouTube URLs\\.\n"
                "Paste a YouTube video link to extract and file its trading knowledge\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await update.message.reply_text(
            f"📚 *Extracting YouTube transcript\\.\\.\\.*\n`{_escape(url[:60])}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        # Extract transcript
        video_id = _extract_youtube_id(url)
        if not video_id:
            await update.message.reply_text(
                "❌ Could not extract video ID from URL\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        video_result = await _get_transcript_for_id(video_id, source_platform="YouTube")

        if not video_result.success or not video_result.transcript:
            err = _escape(video_result.error[:120] if video_result.error else "No transcript available")
            await update.message.reply_text(
                f"⚠️ *No transcript found for this video\\.*\n_{err}_\n\n"
                f"Some videos don't have auto\\-generated captions\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        title = video_result.video_title or "Unknown Title"
        channel = video_result.channel_name or "Unknown Channel"
        char_count = len(video_result.transcript)

        await update.message.reply_text(
            f"📖 *Transcript extracted* \\({char_count:,} chars\\)\n"
            f"_{_escape(title[:60])}_\n"
            f"Channel: {_escape(channel)}\n\n"
            f"Analyzing against knowledge base\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        # Run the knowledge learner
        try:
            report = await self._knowledge_learner.learn(
                transcript=video_result.transcript,
                video_title=title,
                channel_name=channel,
                source_url=url,
                track=force_track,
            )
            await update.message.reply_text(
                report.format_telegram_reply(),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            log.error(f"Knowledge learner error: {e}")
            await update.message.reply_text(
                f"❌ Knowledge extraction failed: {_escape(str(e))}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )

    async def _resolve_pinned_markets(self, series_tickers: list[str]) -> list[dict]:
        """
        Convert a list of series tickers into pinned market dicts for the pipeline.
        Looks up the currently-active contract from the engine's seeded watchlist,
        then fetches fresh price data for it.
        """
        pinned: list[dict] = []
        if not series_tickers:
            return pinned

        for ticker in series_tickers:
            try:
                # Get the currently-seeded active contract ID
                market_id = self.engine.market_data.get_series_market_id(ticker)
                if not market_id:
                    log.warning(f"[TAG] No active contract seeded for {ticker} — skipping pin")
                    continue

                # Fetch fresh market data
                real_ex = getattr(self.engine.exchange, "real", self.engine.exchange)
                data = await real_ex._request("GET", f"/markets/{market_id}")
                m = data.get("market", data)

                yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
                pinned.append({
                    "id": market_id,
                    "title": m.get("title", ticker),
                    "description": m.get("rules_primary", ""),
                    "exchange": "kalshi",
                    "yes_price": yes_ask or 0.5,
                    "volume": float(m.get("volume_fp", m.get("volume_24h_fp", 0)) or 0),
                    "end_date": m.get("close_time"),
                    "floor_strike": m.get("floor_strike"),
                    "category": ticker,
                })
                log.info(f"[TAG] Pinned {ticker} → {market_id} (Yes={yes_ask:.2f})")

            except Exception as e:
                log.warning(f"[TAG] Could not resolve {ticker}: {e}")

        return pinned

    async def _run_pipeline(
        self,
        update: Update,
        content: str,
        source_platform: str,
        pinned_markets: list[dict] | None = None,
        search_hints: list[str] | None = None,
        force_swarm: bool = False,
    ):
        """Run the agent pipeline and send results back to Telegram."""

        async def on_progress(msg: str):
            """Send progress update to Telegram."""
            try:
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception:
                try:
                    # Fallback without markdown if parse fails
                    await update.message.reply_text(msg)
                except Exception:
                    pass

        try:
            result = await self._pipeline.process(
                content=content,
                source_platform=source_platform,
                on_progress=on_progress,
                pinned_markets=pinned_markets or [],
                search_hints=search_hints or [],
                force_swarm=force_swarm,
            )

            # Inject generated signals into the engine's signal queue
            for signal in result.signals:
                self.engine._add_signal(signal)
                # Also execute through risk manager
                asyncio.create_task(self.engine._execute_signal(signal))

            # Log to DB
            if self.engine.db:
                try:
                    await self.engine.db.log_agent_input(
                        source_platform=source_platform,
                        raw_content=content[:500],
                        extracted_text=content[:500],
                        research_summary=result.research.output if result.research else "",
                        trading_thesis=result.synthesis.data.get("trading_thesis", "") if result.synthesis else "",
                        agent_id=(result.signals[0].agent_id if result.signals else "pipeline_news_trader"),
                        signals_generated=len(result.signals),
                        success=result.success,
                        error=result.error,
                        elapsed_seconds=result.total_elapsed,
                    )
                except Exception as e:
                    log.warning(f"Could not log agent input: {e}")

            # Send final summary
            final_reply = result.format_telegram_reply()
            try:
                await update.message.reply_text(final_reply, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception:
                await update.message.reply_text(
                    f"Pipeline complete: {len(result.signals)} signals generated\\. "
                    f"Total time: {result.total_elapsed:.1f}s",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            await update.message.reply_text(
                f"❌ Pipeline error: {_escape(str(e))}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
