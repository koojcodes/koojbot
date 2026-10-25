import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.logging import RichHandler
from rich.console import Console

console = Console()

# Regex to strip Rich markup formatting tags like [bold], [dim], [bold green], [/].
# Deliberately does NOT strip content labels like [BTC15M], [KB], [SOURCE PROPOSAL]
# which the dashboard color-coding depends on.
#
# Strategy: a Rich style tag contains ONLY known style keywords, colors, and spaces
# (e.g. "bold green", "dim", "bright_red", "on_blue").  Content labels contain
# uppercase letters and digits (e.g. "BTC15M", "KB", "DAY PIPELINE") — those are
# left untouched.
_RICH_STYLE_WORD = (
    r'(?:bold|dim|italic|underline|strike|blink|reverse|'
    r'black|red|green|yellow|blue|magenta|cyan|white|'
    r'bright_(?:black|red|green|yellow|blue|magenta|cyan|white)|'
    r'on_\w+|link\S*)'
)
_RICH_TAG_RE = re.compile(
    r'\[/?(?:' + _RICH_STYLE_WORD + r'(?:\s+' + _RICH_STYLE_WORD + r')*' + r')?\]',
    re.IGNORECASE,
)


def _strip_rich(text: str) -> str:
    return _RICH_TAG_RE.sub('', text)


class BroadcastHandler(logging.Handler):
    """Additive handler that buffers log lines and broadcasts them over WebSocket.

    Does NOT interfere with existing Rich console output — it is purely additive.
    """

    def emit(self, record: logging.LogRecord):
        try:
            # Import here to avoid circular imports at module load time
            from bot.utils.log_broadcaster import broadcaster  # noqa: PLC0415

            raw_msg = self.format(record)
            clean_msg = _strip_rich(raw_msg)

            line = json.dumps({
                "t": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "msg": clean_msg,
            })

            # Always buffer synchronously — safe from any thread
            broadcaster.record(line)

            # Schedule async broadcast if a running loop exists
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(
                        lambda ln=line: asyncio.ensure_future(broadcaster.broadcast(ln))
                    )
            except RuntimeError:
                # No event loop yet — silently buffer only
                pass
        except Exception:
            self.handleError(record)


def setup_logger(name: str = "maxthetrader", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # ── Console handler (Rich) ─────────────────────────────────────────────────
    handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    handler.setLevel(level)
    fmt = logging.Formatter("%(message)s", datefmt="[%X]")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    # ── File handler — timestamped log file ────────────────────────────────────
    # Creates logs/bot_YYYYMMDD_HHMM.log on each fresh start.
    # This file is the reliable way to verify a genuine new bot run.
    logs_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_filename = logs_dir / f"bot_{datetime.now().strftime('%Y%m%d_%H%M')}.log"
    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(file_handler)

    # ── WebSocket broadcast handler ────────────────────────────────────────────
    broadcast_handler = BroadcastHandler()
    broadcast_handler.setLevel(level)
    broadcast_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(broadcast_handler)

    return logger


log = setup_logger()
