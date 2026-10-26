"""NFL Telegram commands — /nfl_live, /nfl_watch, /nfl_stop, /nfl_status."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from bot.core.signals import Signal
from bot.utils.logger import log

if TYPE_CHECKING:
    from bot.nfl.engine import NFLSwingEngine


class NFLTelegramHandler:
    """Handles NFL-related Telegram commands and trade alerts."""

    def __init__(self, engine: "NFLSwingEngine"):
        self.engine = engine
        self._task: asyncio.Task | None = None

    def register(self, bot) -> None:
        """Register NFL commands with the Telegram bot."""
        try:
            from telegram import Update
            from telegram.ext import CommandHandler, ContextTypes

            async def nfl_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
                await self._handle_live(update)

            async def nfl_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
                game_id = ctx.args[0] if ctx.args else None
                await self._handle_watch(update, game_id)

            async def nfl_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
                await self._handle_stop(update)

            async def nfl_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
                await self._handle_status(update)

            bot.add_handler(CommandHandler("nfl_live", nfl_live))
            bot.add_handler(CommandHandler("nfl_watch", nfl_watch))
            bot.add_handler(CommandHandler("nfl_stop", nfl_stop))
            bot.add_handler(CommandHandler("nfl_status", nfl_status))
            log.info("[NFL Telegram] Commands registered")
        except ImportError:
            log.warning("[NFL Telegram] python-telegram-bot not installed, skipping")

    async def _handle_live(self, update) -> None:
        games = self.engine.feed.get_live_games()
        if not games:
            await update.message.reply_text("No live NFL games right now.")
            return

        lines = ["🏈 **Live NFL Games**\n"]
        for g in games:
            wp = g.home_win_probability
            velocity = self.engine.feed.get_velocity(g.game_id)
            vel_str = ""
            if velocity:
                vel_str = " | Vel: " + ", ".join(f"{t} {p}pts" for t, p in velocity.items())
            lines.append(
                f"`{g.away_abbrev} {g.away_score}` @ `{g.home_abbrev} {g.home_score}` "
                f"Q{g.quarter} {g.time_remaining}\n"
                f"  Home WP: {wp:.1%}{vel_str}\n"
                f"  ID: `{g.game_id}`"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _handle_watch(self, update, game_id: str | None) -> None:
        if not game_id:
            await update.message.reply_text("Usage: /nfl_watch <game_id>\nGet IDs from /nfl_live")
            return

        if self._task and not self._task.done():
            await update.message.reply_text("Already tracking. /nfl_stop first.")
            return

        await update.message.reply_text(f"Tracking game {game_id}...")
        self._task = asyncio.create_task(self.engine.run_game(game_id))

    async def _handle_stop(self, update) -> None:
        self.engine.stop()
        if self._task and not self._task.done():
            self._task.cancel()
        await update.message.reply_text(
            f"NFL tracking stopped. {self.engine.trade_count} trades placed."
        )

    async def _handle_status(self, update) -> None:
        trades = self.engine.trades
        if not trades:
            await update.message.reply_text("No NFL trades this session.")
            return

        lines = [f"📊 **NFL Session** — {len(trades)} trades\n"]
        for t in trades[-10:]:  # last 10
            sig = t["signal"]
            lines.append(
                f"`{sig['action'].upper()} {sig['outcome']}` on {sig['market_title']}\n"
                f"  Conf: {sig['confidence']:.0%} | {sig['reasoning'][:60]}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def on_trade_alert(self, signal: Signal, executed: bool) -> None:
        """Callback for trade alerts — wire this into the engine."""
        # This would send to the user's Telegram chat
        # Requires the bot instance and chat_id to be wired in
        status = "EXECUTED" if executed else "REJECTED"
        log.info(
            f"[NFL Alert] {status}: {signal.action.value.upper()} {signal.outcome} "
            f"on {signal.market_title} — {signal.reasoning}"
        )
