from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path, override=True)


def _bool(val: str | None) -> bool:
    return str(val).lower() in ("true", "1", "yes")


def _float(val: str | None, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# --- Kalshi ---
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
KALSHI_ENV = os.getenv("KALSHI_ENV", "demo")  # "demo" or "prod"
KALSHI_BASE_URL = (
    "https://demo-api.kalshi.co/trade-api/v2"
    if KALSHI_ENV == "demo"
    else "https://api.elections.kalshi.com/trade-api/v2"
)

# --- Polymarket ---
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"

# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- News ---
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# --- Firecrawl ---
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
]

# --- Trading ---
PAPER_MODE = _bool(os.getenv("PAPER_MODE", "true"))
INITIAL_BALANCE = _float(os.getenv("INITIAL_BALANCE"), 10_000.0)
MAX_POSITION_PCT = _float(os.getenv("MAX_POSITION_PCT"), 0.02)
MAX_EXPOSURE_PCT = _float(os.getenv("MAX_EXPOSURE_PCT"), 0.10)
DAILY_LOSS_LIMIT_PCT = _float(os.getenv("DAILY_LOSS_LIMIT_PCT"), 0.05)
STOP_LOSS_PCT = _float(os.getenv("STOP_LOSS_PCT"), 0.05)

# --- Multi-agent runtime toggles ---
AGENT_ENABLE_BTC15M = _bool(os.getenv("AGENT_ENABLE_BTC15M", "true"))
AGENT_ENABLE_POLYMARKET_NEWS = _bool(os.getenv("AGENT_ENABLE_POLYMARKET_NEWS", "false"))
AGENT_ENABLE_STOCKS_ETF_SHELL = _bool(os.getenv("AGENT_ENABLE_STOCKS_ETF_SHELL", "false"))
AGENT_POLY_MIN_VOLUME = _float(os.getenv("AGENT_POLY_MIN_VOLUME"), 10_000.0)
AGENT_POLY_MAX_SPREAD = _float(os.getenv("AGENT_POLY_MAX_SPREAD"), 0.08)
AGENT_POLY_MIN_MINUTES_TO_CLOSE = int(os.getenv("AGENT_POLY_MIN_MINUTES_TO_CLOSE", "60"))

# --- BTC15M / day-trader thresholds (paper observation knobs) ---
# Defaults match production-style filters. Lower these in .env while paper-testing
# so you can observe fills without waiting for rare high-edge setups.
BTC15M_MIN_VS_STRIKE_PCT = _float(os.getenv("BTC15M_MIN_VS_STRIKE_PCT"), 0.0008)
BTC15M_MIN_NET_EDGE = _float(os.getenv("BTC15M_MIN_NET_EDGE"), 0.08)
BTC15M_MIN_CONFIDENCE = _float(os.getenv("BTC15M_MIN_CONFIDENCE"), 0.60)
DAY_TRADER_MIN_EDGE = _float(os.getenv("DAY_TRADER_MIN_EDGE"), 0.05)
DAY_TRADER_MIN_CONFIDENCE = _float(os.getenv("DAY_TRADER_MIN_CONFIDENCE"), 0.55)
DAY_TRADER_MIN_VOLUME = _float(os.getenv("DAY_TRADER_MIN_VOLUME"), 2_000.0)

# --- Exchange-specific risk overrides ---
RISK_POLY_MAX_POSITION_PCT = _float(
    os.getenv("RISK_POLY_MAX_POSITION_PCT"),
    0.01,
)
RISK_POLY_MAX_EXPOSURE_PCT = _float(
    os.getenv("RISK_POLY_MAX_EXPOSURE_PCT"),
    0.05,
)

# --- Polymarket copy trading ---
AGENT_ENABLE_POLYMARKET_COPY = _bool(os.getenv("AGENT_ENABLE_POLYMARKET_COPY", "false"))
POLY_COPY_LEADERS = [
    w.strip().lower()
    for w in os.getenv("POLY_COPY_LEADERS", "").split(",")
    if w.strip().startswith("0x")
]
POLY_COPY_POLL_INTERVAL = _float(os.getenv("POLY_COPY_POLL_INTERVAL"), 15.0)
POLY_COPY_MAX_LAG_SECONDS = _float(os.getenv("POLY_COPY_MAX_LAG_SECONDS"), 120.0)
POLY_COPY_SIZING_MODE = os.getenv("POLY_COPY_SIZING_MODE", "fixed_usd")  # fixed_usd | pct_leader | pct_portfolio
POLY_COPY_FIXED_USD = _float(os.getenv("POLY_COPY_FIXED_USD"), 25.0)
POLY_COPY_SIZING_PCT = _float(os.getenv("POLY_COPY_SIZING_PCT"), 0.10)
POLY_COPY_MAX_SLIPPAGE = _float(os.getenv("POLY_COPY_MAX_SLIPPAGE"), 0.02)
POLY_COPY_MAX_POSITIONS_PER_LEADER = int(os.getenv("POLY_COPY_MAX_POSITIONS_PER_LEADER", "5"))
POLY_COPY_MAX_DAILY_LOSS_PER_LEADER = _float(os.getenv("POLY_COPY_MAX_DAILY_LOSS_PER_LEADER"), 100.0)
POLYMARKET_DATA_API_URL = os.getenv("POLYMARKET_DATA_API_URL", "https://data-api.polymarket.com")
POLYMARKET_USER_ADDRESS = os.getenv("POLYMARKET_USER_ADDRESS", "")

# --- Daily P&L report ---
DAILY_REPORT_TELEGRAM = _bool(os.getenv("DAILY_REPORT_TELEGRAM", "true"))
DAILY_REPORT_HOUR_UTC = int(os.getenv("DAILY_REPORT_HOUR_UTC", "9"))
DAILY_REPORT_OUTPUT_DIR = os.getenv(
    "DAILY_REPORT_OUTPUT_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "logs" / "reports"),
)

# --- API Server ---
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# --- Database ---
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "maxthetrader.db"

# --- Swarm Intelligence (Zep + OASIS) ---
SWARM_ENABLED = _bool(os.getenv("SWARM_ENABLED", "false"))
ZEP_API_KEY = os.getenv("ZEP_API_KEY", "")
SWARM_LLM_API_KEY = os.getenv("SWARM_LLM_API_KEY", "")
SWARM_LLM_BASE_URL = os.getenv("SWARM_LLM_BASE_URL", "https://api.openai.com/v1")
SWARM_LLM_MODEL_NAME = os.getenv("SWARM_LLM_MODEL_NAME", "gpt-4o-mini")

# --- Phase 6: Auto-Triggers & Learning Loop ---
SWARM_AUTO_TRIGGER = _bool(os.getenv("SWARM_AUTO_TRIGGER", "true"))  # Auto-fire sims on high-impact events
SWARM_DAILY_SIM_HOUR = int(os.getenv("SWARM_DAILY_SIM_HOUR", "8"))  # UTC hour for daily sims
SWARM_DAILY_SIM_MAX = int(os.getenv("SWARM_DAILY_SIM_MAX", "3"))    # Max daily sims
