"""Risk management — enforces position limits, circuit breakers, stop losses, and time exits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from bot.config import settings
from bot.core.signals import Signal, SignalAction
from bot.exchanges.base import Exchange, Position, Side
from bot.utils.logger import log


@dataclass
class RiskCheck:
    approved: bool
    adjusted_size: float
    reason: str = ""


class RiskManager:
    """
    Enforces risk limits before trade execution.

    Rules:
    - Max position size as % of capital per trade
    - Max total exposure across all positions
    - Daily loss circuit breaker
    - Per-trade stop loss
    - Maximum hold time (time-based exit)
    """

    def __init__(
        self,
        max_position_pct: float = 0.0,
        max_exposure_pct: float = 0.0,
        daily_loss_limit_pct: float = 0.0,
        stop_loss_pct: float = 0.0,
        max_hold_hours: float = 4.0,   # time-based exit: sell after N hours
    ):
        self.max_position_pct = max_position_pct or settings.MAX_POSITION_PCT
        self.max_exposure_pct = max_exposure_pct or settings.MAX_EXPOSURE_PCT
        self.daily_loss_limit_pct = daily_loss_limit_pct or settings.DAILY_LOSS_LIMIT_PCT
        self.stop_loss_pct = stop_loss_pct or settings.STOP_LOSS_PCT
        self.max_hold_hours = max_hold_hours
        self._daily_realized_pnl: dict[str, float] = {}
        self._leader_daily_pnl: dict[str, float] = {}
        self._circuit_breaker_active = False

    def _risk_profile(self, signal: Signal) -> tuple[float, float]:
        exchange_name = (signal.execution_exchange or signal.exchange or "").lower()
        if "polymarket" in exchange_name:
            return (
                settings.RISK_POLY_MAX_POSITION_PCT,
                settings.RISK_POLY_MAX_EXPOSURE_PCT,
            )
        return (self.max_position_pct, self.max_exposure_pct)

    async def check_signal(
        self,
        signal: Signal,
        exchange: Exchange,
    ) -> RiskCheck:
        """Evaluate a signal against risk rules. Returns approval and adjusted size."""

        if not signal.is_actionable:
            return RiskCheck(approved=False, adjusted_size=0, reason="Signal not actionable")

        if self._circuit_breaker_active:
            return RiskCheck(approved=False, adjusted_size=0, reason="Circuit breaker active — daily loss limit reached")

        if signal.agent_id == "polymarket_copy" and signal.leader_wallet:
            leader_loss = self._leader_daily_pnl.get(signal.leader_wallet, 0.0)
            if leader_loss < -settings.POLY_COPY_MAX_DAILY_LOSS_PER_LEADER:
                return RiskCheck(
                    approved=False,
                    adjusted_size=0,
                    reason=f"Leader daily loss cap reached (${abs(leader_loss):.2f})",
                )

        balance = await exchange.get_balance()
        positions = await exchange.get_positions()
        profile_max_position_pct, profile_max_exposure_pct = self._risk_profile(signal)

        # Calculate current exposure
        total_exposure = sum(p.size * p.avg_entry_price for p in positions)
        exposure_pct = total_exposure / balance if balance > 0 else 1.0

        # Max exposure check
        if exposure_pct >= profile_max_exposure_pct:
            log.warning(f"[bold red]RISK[/] Max exposure reached: {exposure_pct:.1%} >= {profile_max_exposure_pct:.1%}")
            return RiskCheck(approved=False, adjusted_size=0, reason=f"Max exposure {profile_max_exposure_pct:.0%} reached")

        # Calculate max position size
        max_position_value = balance * profile_max_position_pct
        remaining_exposure = (profile_max_exposure_pct * balance) - total_exposure
        max_value = min(max_position_value, remaining_exposure)

        if signal.target_price and signal.target_price > 0:
            max_size = max_value / signal.target_price
        else:
            max_size = max_value

        # Use signal's suggestion if within limits
        if signal.size_suggestion and signal.size_suggestion <= max_size:
            size = signal.size_suggestion
        else:
            size = max_size

        if size <= 0:
            return RiskCheck(approved=False, adjusted_size=0, reason="Calculated size is zero")

        # Scale by confidence
        size = size * signal.confidence

        log.info(
            f"[green]RISK OK[/] Size: {size:.2f}, "
            f"exposure: {exposure_pct:.1%}/{profile_max_exposure_pct:.1%}, "
            f"balance: ${balance:.2f}"
        )

        return RiskCheck(approved=True, adjusted_size=size)

    def record_pnl(self, pnl: float, leader_wallet: str = ""):
        """Record realized P&L and check circuit breaker."""
        today = date.today().isoformat()
        self._daily_realized_pnl[today] = self._daily_realized_pnl.get(today, 0.0) + pnl
        if leader_wallet:
            key = f"{today}:{leader_wallet.lower()}"
            self._leader_daily_pnl[leader_wallet.lower()] = (
                self._leader_daily_pnl.get(leader_wallet.lower(), 0.0) + pnl
            )

        daily_pnl = self._daily_realized_pnl[today]
        if daily_pnl < 0 and abs(daily_pnl) > self.daily_loss_limit_pct * settings.INITIAL_BALANCE:
            self._circuit_breaker_active = True
            log.error(
                f"[bold red]CIRCUIT BREAKER[/] Daily loss ${abs(daily_pnl):.2f} "
                f"exceeds limit of {self.daily_loss_limit_pct:.0%} of capital. Trading paused."
            )

    def reset_daily(self):
        """Reset daily P&L tracking. Call at start of each trading day."""
        self._circuit_breaker_active = False

    async def check_stop_losses(self, exchange: Exchange) -> list[Signal]:
        """
        Check all positions against stop losses and time-based exits.
        Returns sell signals for any positions that should be exited.
        """
        positions = await exchange.get_positions()
        exit_signals: list[Signal] = []
        now = datetime.now(timezone.utc)

        for pos in positions:
            # 1. Price-based stop loss
            if pos.current_price > 0:
                loss_pct = (pos.avg_entry_price - pos.current_price) / pos.avg_entry_price
                if loss_pct >= self.stop_loss_pct:
                    log.warning(
                        f"[bold red]STOP LOSS[/] {pos.market_title} ({pos.outcome}) "
                        f"loss: {loss_pct:.1%} >= {self.stop_loss_pct:.1%}"
                    )
                    exit_signals.append(Signal(
                        market_id=pos.market_id,
                        market_title=pos.market_title,
                        exchange=pos.exchange,
                        action=SignalAction.SELL,
                        outcome=pos.outcome,
                        confidence=1.0,
                        reasoning=f"Stop loss triggered: {loss_pct:.1%} loss (limit: {self.stop_loss_pct:.1%})",
                        strategy="risk_manager",
                    ))
                    continue  # No need to check time exit if we're already selling

            # 2. Time-based exit
            if self.max_hold_hours > 0 and pos.entered_at is not None:
                entered = pos.entered_at
                if entered.tzinfo is None:
                    entered = entered.replace(tzinfo=timezone.utc)
                age_hours = (now - entered).total_seconds() / 3600
                if age_hours >= self.max_hold_hours:
                    log.warning(
                        f"[bold yellow]TIME EXIT[/] {pos.market_title} ({pos.outcome}) "
                        f"held {age_hours:.1f}h >= max {self.max_hold_hours}h"
                    )
                    exit_signals.append(Signal(
                        market_id=pos.market_id,
                        market_title=pos.market_title,
                        exchange=pos.exchange,
                        action=SignalAction.SELL,
                        outcome=pos.outcome,
                        confidence=1.0,
                        reasoning=f"Max hold time reached: {age_hours:.1f}h (limit: {self.max_hold_hours}h)",
                        strategy="risk_manager",
                    ))

        return exit_signals
