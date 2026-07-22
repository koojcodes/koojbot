# MaxTheTrader — Architecture & Build Log

> Last updated: 2026-04-10 (session 8 — agent-targeted KB + clean reset)
> Full system overview of every component built across all development sessions.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture Diagram](#2-system-architecture-diagram)
3. [Core Trading Engine](#3-core-trading-engine)
4. [Telegram Bot](#4-telegram-bot)
5. [Multi-Agent Pipeline (News/Signal)](#5-multi-agent-pipeline-newssignal)
6. [Day Trader Framework](#6-day-trader-framework)
7. [Adaptive Knowledge Base](#7-adaptive-knowledge-base)
8. [Data & Infrastructure](#8-data--infrastructure)
9. [Market Selection & Watchlist Management](#9-market-selection--watchlist-management)
10. [Tag System Reference](#10-tag-system-reference)
11. [Running the Bot](#11-running-the-bot)
12. [File Index](#12-file-index)
13. [Session 7 Multi-Agent Runtime](#13-session-7-multi-agent-runtime)

---

## 1. Project Overview

MaxTheTrader is an automated prediction market trading bot targeting Kalshi and Polymarket. It combines:

- **Autonomous trading engine** that runs strategy cycles every N seconds
- **Telegram bot interface** for human-in-the-loop signal injection and monitoring
- **Multi-agent pipeline** (Researcher → Synthesizer → Trader) that converts social media content and news into trading signals
- **Day trader framework** with market-specific configs and a live BTC price feed
- **Adaptive per-agent knowledge base** that routes learning to specialized agents and continuously refines each agent's strategy

---

## 2. System Architecture Diagram

```
User's Phone (Telegram)
        │
        ├── /status, /portfolio, /positions, /signals, /trades
        ├── /knowledge  ← view accumulated trading knowledge
        │
        ├── #learn <YouTube URL>
        │       └── KnowledgeLearnerAgent ──────────────────────┐
        │               ↓ transcript                             │
        │           Cross-analyze vs KB                          ▼
        │           ADD / CONFIRM / UPDATE / CONTRADICT    knowledge_store/
        │                                                  (7 JSON documents)
        │                                                        │
        ├── #news <any URL>  ──────────────────────────┐         │
        │   #news #btc15m <URL>                        │         │
        │   #btc15m <TikTok/Instagram URL>             ▼         │
        │       └── ContentFetcher                AgentPipeline  │
        │               ↓ og:description          (Researcher    │
        │           VideoFetcher                  + Synthesizer  │
        │               ↓ YouTube search          + Trader)      │
        │           Transcript extraction              │          │
        │               ↓                         Signals        │
        │           Pipeline content                  │          │
        │                                             ▼          │
        └── Bare YouTube URL                    Engine._signals  │
                └── Prompt: add #learn or #news       │          │
                                                      ▼          │
                                               RiskManager       │
                                                      │          │
                                                      ▼          │
                                              KalshiExchange     │
                                             (or PaperExchange)  │
                                                                  │
Engine (autonomous cycle every 30s)                              │
        │                                                         │
        ├── Standard strategies (NewsStrategy, MomentumStrategy) │
        │                                                         │
        └── DayTraderPipeline (--day-trader flag)                │
                │                                                 │
                ├── scan_configured_only() every cycle           │
                │       └── BTC15MConfig.generate_signal()       │
                │               ├── Live BTC price (Coinbase)    │
                │               ├── 5-filter signal logic        │
                │               └── Reads knowledge_store/ ◄─────┘
                │
                └── _reseed_day_trader_markets() every 30 cycles
                        └── Finds active KXBTC15M contract ID
```

Note: The legacy pooled knowledge docs were reset. Current learning writes into per-agent stores under `knowledge_store/agents/<agent_id>/<track>/`.

---

## Claude Orchestration Layer

Top-layer Claude Code agent prompts are defined under `.Claude/agents/`:

- `orchestrator.md` coordinates learning, validation, and trader dispatch.
- `knowledge_builder.md` creates candidate knowledge updates.
- `knowledge_validator.md` promotes/rejects knowledge candidates.
- `btc15m_trader.md` and `macro_event_trader.md` generate bounded trade intents.
- `contracts.md` defines strict JSON output schemas across all agents.

Design choice: learning is centralized and validated before trader agents consume
it. Trader agents remain execution-focused and do not write global knowledge.

---

## 3. Core Trading Engine

### `bot/core/engine.py`
The central trading loop. Runs every `poll_interval` seconds (default 30s).

**Key additions:**
- `day_trader_pipeline` parameter — if set, runs `scan_configured_only()` every cycle
- `_DAY_TRADER_RESEED_EVERY_N_CYCLES = 30` — re-seeds series contracts every ~15 minutes
- `_reseed_day_trader_markets()` — finds the currently-open KXBTC15M contract and rotates expired ones out of the watchlist
- `_run_day_trader_cycle()` — calls the pipeline and injects any resulting signals

### `bot/strategies/momentum.py`, `bot/strategies/news_driven.py`
Original strategies. Still run on every cycle. DayTrader is additive, not replacing.

---

## 4. Telegram Bot

### `bot/telegram_bot/bot.py`
Main bot handler. Runs non-blocking alongside the trading engine via `asyncio.gather`.

**Commands:**

| Command | Description |
|---|---|
| `/start` | Welcome + command list |
| `/status` | Running state, mode, cycle count, P&L |
| `/portfolio` | Balance, exposure, realized/unrealized P&L |
| `/positions` | Open positions with entry/current price |
| `/signals` | Last 5 signals |
| `/trades` | Last 10 trades |
| `/knowledge` | Knowledge base summary — rules per category, top insights |
| `/watchlist` | View + manage monitored markets (approve/remove inline buttons) |
| `/pause` | Pause automated trading |
| `/resume` | Resume trading |

**Message routing — auto-classification (no tags required):**

Every URL or text message is automatically classified by a lightweight Claude
call (~150 input tokens) into one of two routes before being processed:

```
Any content received (URL or text)
        │
        ├── Explicit #learn tag → always → KnowledgeLearnerAgent
        ├── Explicit #news tag  → always → AgentPipeline (signals)
        │
        └── No intent tag → _auto_classify(content, platform)
                │                    ↓
                │            claude-haiku-4-5
                │            "KNOWLEDGE or SIGNAL?"
                │
                ├── KNOWLEDGE → KnowledgeLearnerAgent → update KB
                └── SIGNAL   → AgentPipeline → trading signals
```

| Input | Auto-route result |
|---|---|
| Reddit post about a trading strategy | → KNOWLEDGE → KB |
| Reddit post about a breaking macro event | → SIGNAL → pipeline |
| YouTube URL (educational) | → KNOWLEDGE → KB (transcript extracted first) |
| TikTok / Instagram URL | → fetch caption + YouTube transcript → classify → route |
| X/Twitter post with price target | → SIGNAL → pipeline |
| Plain text describing a trade setup | → KNOWLEDGE → KB |
| Plain text: "BTC just broke 70k" | → SIGNAL → pipeline |
| `#btc15m <any URL>` | → classify → route, KXBTC15M pinned into Researcher |
| `#btc15m` plain text | → **blocked** — 15-min markets need a URL |
| `#learn <YouTube URL>` | → explicit override → always KNOWLEDGE |
| `#news <any URL>` | → explicit override → always SIGNAL |

**Security:** Only processes messages from `TELEGRAM_ALLOWED_USER_IDS` in `.env`.

### `bot/telegram_bot/content_fetcher.py`
Platform-aware content extractor. Strategy per platform:

**Reddit** — Uses the Reddit JSON API (`{post_url}.json`) instead of HTML
scraping. Reddit's pages are JS-rendered and return almost nothing via OG tags.
The JSON API returns the full post title, selftext body, and top N comments by
score — assembled into a single rich text block.

```
_fetch_reddit_post(url)
  ├── Convert post URL → .json URL via _reddit_json_url()
  ├── GET with Reddit User-Agent header (required to avoid 429)
  ├── Extract: title, selftext, author, subreddit, score
  └── Top 5 comments (by score, up to 800 chars total)
      → Returns FetchedContent with full assembled text
```

**TikTok / Instagram / Facebook** — `og:description` caption, then searches
YouTube for the same video to extract its spoken transcript (via `video_fetcher`).
The `FetchedContent` dataclass carries:
- `transcript` — spoken transcript text (preferred over caption when present)
- `found_on_youtube` — bool
- `video_title`, `channel_name`, `youtube_url`
- `transcript_error` — reason transcript was unavailable (if attempted)

**All other URLs** — `og:description` → `twitter:description` → title + paragraph fallback.

### `bot/telegram_bot/video_fetcher.py`
Two-step workflow for extracting spoken content from short-form video posts.

1. **YouTube URLs** → extract transcript directly via `youtube-transcript-api`
2. **TikTok / Instagram** → search YouTube via `youtubesearchpython` for the same creator + video → extract transcript

Key functions:
- `fetch_video_transcript(url, source_platform, caption_text)` — main entry point
- `_extract_creator(url, platform)` — regex extracts username from URL
- `_extract_keywords(caption, max_words=8)` — strips hashtags, emoji, filler words
- `_build_search_query(creator, keywords, platform)` — builds YouTube search string

**Libraries (no API keys):** `youtube-transcript-api`, `youtube-search-python`

### `bot/telegram_bot/tag_parser.py`
Parses `#tags` from Telegram messages into optional intent overrides and market targets.

```python
@dataclass
class ParsedTags:
    intent: str | None          # "learn" | "news" | None (optional overrides)
    series_tickers: list[str]   # e.g. ["KXBTC15M"]
    search_hints: list[str]     # e.g. ["btc", "crypto"]
    raw_tags: list[str]
    clean_text: str
```

**Tag registry:**

| Tag | Type | Effect |
|---|---|---|
| `#learn` | Intent override | Force knowledge ingestion — bypasses classifier |
| `#news` | Intent override | Force signal pipeline — bypasses classifier |
| `#btc15m` | Market | `KXBTC15M` — pins active BTC 15-min contract into Researcher |
| `#kxbtc15m` | Market | `KXBTC15M` |
| `#btc` | Search hint | Broad BTC topic for Researcher |
| `#eth` | Search hint | Broad ETH topic |
| `#crypto` | Search hint | Broad crypto topic |
| `#fed`, `#rates`, `#cpi`, `#macro` | Search hints | Macro topics |

`URL_ONLY_SERIES = {"KXBTC15M", "KXETH15M"}` — plain text with these tags is blocked (resolves too fast).

---

## 5. Multi-Agent Pipeline (News/Signal)

Sequential 3-agent pipeline triggered when content is classified as SIGNAL.

### `bot/agents/pipeline.py` — `AgentPipeline`
Orchestrates Researcher → Synthesizer → Trader. Sends real-time progress updates to Telegram via `on_progress` callback.

Accepts `pinned_markets: list[dict]` and `search_hints: list[str]` from the tag system so that `#btc15m` tagged messages inject the live active KXBTC15M contract directly into the Researcher's context.

### `bot/agents/researcher.py` — `ResearcherAgent`
Uses Claude (claude-sonnet-4-5-20250929) with tool use to:
- `search_news(query)` → calls `NewsCollector.fetch_news()`
- `search_markets(query)` → calls `MarketDataFetcher.search_markets()`

**Content types:** `news`, `trading_advice`, `analysis`, `day_trading_strategy`, `other`

For `day_trading_strategy` content: extracts entry/exit conditions, asset, timeframe, and signal type rather than probability deltas.

Pinned markets are pre-loaded into `collected_markets` before the agentic loop runs, guaranteeing they appear in Synthesizer output even if Claude doesn't search for them. Markets closing within 30 minutes are tagged `⚡ closes in Xmin`.

### `bot/agents/synthesizer.py` — `SynthesizerAgent`
Pure Claude reasoning — no tools. Converts Researcher output into a trading thesis.

**Two prompt modes:**
- Standard: outputs `trading_thesis`, `market_implications` (probability deltas), `confidence`, `time_horizon`
- `day_trading_strategy`: outputs `strategy_rules` (entry conditions, exit conditions, signal type, timeframe) + whether current conditions match the strategy

Short-term markets (titles containing "15 min", "up in next") are tagged `⚡` and Claude is instructed to use `time_horizon: "minutes"` only if the content implies an immediate move.

### `bot/agents/trader.py` — `TraderAgent`
Deterministic (no Claude). Converts `SynthesisResult.market_implications` into `Signal` objects and feeds them through `ConvictionScorer`.

### `bot/agents/base.py`
Abstract `Agent` base class + `AgentResult` dataclass.

---

## 6. Day Trader Framework

Autonomous framework for ultra-short-term prediction market trading. Currently configured for the Kalshi BTC 15-minute market (KXBTC15M).

### `bot/agents/day_trader_pipeline.py` — `DayTraderPipeline`
- `scan_configured_only()` — fast path: only runs registered `MarketConfig` objects (no Claude API call unless signal found)
- `register_market_config(config)` — adds a market config
- `_run_configured_markets()` — calls `config.generate_signal()` for each registered market
- `_build_market_context(config)` — fetches live BTC price snapshot + active contract data

### `bot/agents/day_trader_researcher.py` — `DayTraderResearcherAgent`
Full Claude-powered scanner for the watchlist. Used for opportunistic scanning.

**Tools:**
- `scan_watched_markets()` — formats all live market snapshots with 30m price changes, time to resolution
- `search_breaking_news(topic)` — searches for articles from the last 2 hours

**On every scan:** injects `format_relevant_context(asset="BTC", timeframe="15m")` from the knowledge base into the system prompt.

### `bot/agents/day_trader.py` — `DayTraderAgent`
Deterministic execution agent.
- `MIN_EDGE = 5%`, `MIN_CONFIDENCE = 55%`
- `STOP_LOSS_PCT = 3%`, `TARGET_TAKE_PCT = 40%`, `MAX_HOLD_HOURS = 1.5`

### `bot/strategies/market_configs/base.py` — `MarketConfig` (ABC)
Abstract base class for market-specific strategies.

```python
class MarketConfig(ABC):
    series_ticker: str = ""
    entry_window: EntryWindow       # open_after_minutes, close_before_minutes
    fee_model: FeeModel             # maker_fee, taker_fee, min_spread
    sizing: PositionSizingRules     # max_position_usd, max_contracts, kelly_fraction

    @abstractmethod
    async def generate_signal(self, context: dict) -> DayTradeOpportunity | None: ...

    @abstractmethod
    async def get_current_market_id(self, exchange_client) -> str | None: ...
```

### `bot/strategies/market_configs/btc15m.py` — `BTC15MConfig`
5-filter signal logic for the KXBTC15M market.

**Market mechanics:**
- Binary market resolving via CF Benchmarks BRTI 60-second average every 15 minutes
- Quadratic fees (~1.6¢/contract at 65¢)
- Round-trip cost ~7.18¢ → minimum **8% net edge** required

**Signal filters (all must pass):**
1. vs-strike magnitude ≥ 1.5% (BTC must be meaningfully above/below the strike)
2. Momentum agreement (short-term momentum confirms the direction)
3. RSI extremes confirmation (RSI < 35 for longs, RSI > 65 for shorts)
4. Net edge after fees ≥ minimum threshold
5. Market price ceiling (don't buy Yes above 85¢ or below 15¢)

**Entry window:** 2.0 min after candle open, 3.0 min before close.

**Fair value estimation:** Logistic model using `vs_strike_pct`, `momentum_5m`, `momentum_1m`.

### `bot/data/btc_price.py` — `BTCPriceFeed`
Live BTC price feed using Coinbase Exchange public API (BRTI constituent) with Kraken fallback.

**10-second cache.** Computes on each fetch:
- `vs_strike(strike)` — % distance from a given strike price
- `momentum_direction` — `"up"` | `"down"` | `"neutral"`
- `momentum_strength` — `"strong"` | `"weak"` | `"none"`
- RSI(14), 1m/5m/10m returns, volume ratio

### `bot/data/market_data.py` — `MarketDataFetcher` additions
- `seed_series_market(series_ticker, exchange_client)` — finds the currently-open KXBTC15M contract, adds to watchlist, rotates out expired ones
- `get_series_market_id(series_ticker)` — returns the current active contract ID
- `_series_contracts: dict[str, str]` — maps series ticker → active market ID

---

## 7. Adaptive Knowledge Base

A continuously-improving system that learns from YouTube/social sources and synthesizes that knowledge into agent-specific frameworks.

### Workflow

```
User: #learn https://youtube.com/watch?v=...
        │
        ▼
_get_transcript_for_id()          (youtube-transcript-api, no API key)
        │
        ▼
KnowledgeLearnerAgent.learn()
        │
        ├── Resolves target agent via detect_target_agent(...)
        ├── Loads agent-specific knowledge context
        ├── Passes transcript + KB to Claude
        │
        └── Claude outputs structured updates:
              ┌─ ADD       → new rule not in KB yet
              ├─ CONFIRM   → agrees with existing rule (confidence +5%, status upgrade)
              ├─ UPDATE    → existing rule needs refinement
              └─ CONTRADICT → conflicts with existing rule (confidence -10%, open question filed)
        │
        ▼
knowledge_base.apply_updates(agent_id=...)    → writes to knowledge_store/agents/<agent_id>/<track>/*.json
        │
        ▼
Telegram report:
  "📚 +3 new rules, ✅2 confirmed, ⚡1 contradiction filed"
```

### `bot/agents/knowledge_learner.py` — `KnowledgeLearnerAgent`
Core learning agent. Single Claude call with full KB context in prompt.

- Deduplication: `has_processed_video(url)` — won't re-process the same video
- Max transcript chars: 6,000 (keeps tokens reasonable)
- Returns `LearningReport` with `format_telegram_reply()` method

### `bot/knowledge/knowledge_base.py`
Persistence layer. Storage: `knowledge_store/agents/` at project root (agent-scoped `.json` files per category).

**Key functions:**
- `load_category(category)` / `save_category(category, data)` — read/write
- `apply_updates(updates, source_url, video_title, channel_name)` — apply a batch of ADD/CONFIRM/UPDATE/CONTRADICT actions
- `format_for_prompt(categories, max_rules_per_cat)` — compact text block for Claude prompt injection
- `format_relevant_context(asset, timeframe)` — filtered slice for DayTraderResearcher
- `has_processed_video(url)` — deduplication check
- `get_all_source_videos()` — deduplicated list of all ingested videos
- `count_total_rules()` — total rules across all categories

### `bot/knowledge/categories.py`
Defines the 7 knowledge categories with descriptions and keyword lists.

| Category | Covers |
|---|---|
| `entry_setup` | Entry trigger conditions, confirmation signals, optimal timing |
| `exit_management` | Stop loss, take profit, trailing stops, time-based exits |
| `market_structure` | Support/resistance, trends, chart patterns, price action |
| `indicator_usage` | RSI, MACD, Bollinger Bands, volume, moving averages |
| `risk_management` | Position sizing, max daily loss, portfolio allocation |
| `market_timing` | Session timing, when to sit out, news-driven windows |
| `btc_specific` | Funding rates, liquidations, on-chain signals, halving effects |

`detect_categories(text)` — heuristically identifies which categories a transcript covers (used to pre-focus Claude's analysis).

### Knowledge document schema

```json
{
  "category": "entry_setup",
  "version": 7,
  "last_updated": "2025-03-31T10:30:00Z",
  "summary": "Synthesis of everything known about entries",
  "consolidated_rules": [
    {
      "id": "rule_001",
      "rule": "RSI below 32 on 15m chart is a strong BTC long entry signal",
      "asset": "BTC",
      "timeframe": "15m",
      "confidence": 0.85,
      "confirmed_by": 3,
      "status": "confirmed"
    }
  ],
  "open_contradictions": [
    {
      "description": "Creator A uses RSI 30, Creator B uses RSI 35 — unresolved",
      "added_date": "2025-03-31"
    }
  ],
  "source_videos": [
    {
      "url": "https://youtube.com/watch?v=...",
      "title": "How I Trade BTC 15m",
      "channel": "TraderGareth",
      "processed_at": "2025-03-31T10:30:00Z"
    }
  ]
}
```

---

## 8. Data & Infrastructure

### `bot/utils/db.py` — SQLite Database
Tables:
- `trades` — executed trade history
- `signals` — generated signal log
- `portfolio_snapshots` — periodic portfolio state
- `agent_inputs` — log of every Telegram-triggered pipeline run (source platform, content, research summary, thesis, signals generated)

Phase 7 adds `agent_id` attribution columns in `trades`, `signals`, and `agent_inputs` so every new row can be tied to the originating runtime agent.

**Key methods:** `log_agent_input()`, `get_agent_inputs()`, `get_trades()`, `log_trade()`

### `bot/api/server.py` — FastAPI REST API
Endpoints include:
- `GET /api/status` — bot status
- `GET /api/portfolio` — portfolio state
- `GET /api/signals` — recent signals
- `GET /api/trades` — trade history
- `GET /api/telegram/inputs` — logged agent pipeline runs (for dashboard)

Phase 7 API additions:
- `GET /api/agents/registry` — current registered agents and metadata
- `GET /api/agents/quality-gates` — per-agent go/no-go thresholds
- `agent_id` query filters on `/api/signals`, `/api/trades`, and `/api/telegram/inputs`

### `bot/config/settings.py`
Key environment variables:

```env
ANTHROPIC_API_KEY=...
KALSHI_API_KEY_ID=...
KALSHI_PRIVATE_KEY=...
POLYMARKET_PRIVATE_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=123456789
PAPER_MODE=true
INITIAL_BALANCE=1000.0
# Multi-agent toggles
AGENT_ENABLE_BTC15M=true
AGENT_ENABLE_POLYMARKET_NEWS=true
AGENT_ENABLE_STOCKS_ETF_SHELL=true
# Polymarket candidate filters
AGENT_POLY_MIN_VOLUME=10000
AGENT_POLY_MAX_SPREAD=0.08
AGENT_POLY_MIN_MINUTES_TO_CLOSE=60
# Polymarket risk overrides
RISK_POLY_MAX_POSITION_PCT=0.01
RISK_POLY_MAX_EXPOSURE_PCT=0.05
```

### `bot/exchanges/paper.py` — `PaperExchange`
Wraps any real exchange for simulated trading. `paper.real` returns the underlying exchange instance.

---

## 9. Market Selection & Watchlist Management

The bot does not trade random markets. Every market it monitors must pass through
one of three explicit gates before it enters the live watchlist.

### Three-tier selection model

```
Tier 1 — Pre-approved series (hardcoded)
│   Defined in ALWAYS_APPROVED_SERIES in bot/data/approved_markets.py
│   Seeded automatically on every startup — no user confirmation needed.
│   Currently: KXBTC15M (BTC 15-Minute Price Direction)
│
Tier 2 — User-approved markets (persisted)
│   Markets the user has explicitly said ✅ to via Telegram.
│   Stored in approved_markets.json at the project root.
│   Restored into the live watchlist on every startup.
│
Tier 3 — Auto-discovered proposals (requires approval)
    Auto-discovery runs at startup and every ~6 hours.
    Any candidate that passes filters is proposed to the user via Telegram
    inline buttons — it is NOT added until the user taps ✅.
    If the user taps ❌, the market is recorded as declined and never
    proposed again.
```

### Auto-discovery filter chain

Runs `_AUTO_DISCOVER_QUERIES` (bitcoin, btc, crypto, federal reserve, inflation,
interest rate) against each exchange, then applies these filters in order:

1. **Title relevance** — the market title must contain at least one word from
   the search query. This post-filter discards unrelated results returned by
   Kalshi's text search (which can return sports parlays for a "bitcoin" query).

2. **Volume floor** — `volume ≥ 5,000`. Low-liquidity markets are skipped.

3. **Time remaining** — market must close more than 2 hours from now.
   Very short-horizon markets (e.g. 15-min windows) are seeded separately via
   `seed_series_market()`, not through auto-discovery.

4. **Known-market check** — via `ApprovedMarketsStore`:
   - Already approved → added to watchlist directly (no re-prompt).
   - Already declined → silently skipped.
   - Unknown → proposed to user via Telegram.

### Telegram approval flow

When a new market clears the filter chain:

```
Bot sends Telegram message:
  📊 New Market Found
  ─────────────────────
  BTC above $70,000 by end of week?
  KXBTC-26MAR3-00
  Volume: $48,200  |  Closes in: 62.4h

  [✅  Monitor]   [❌  Skip]

User taps ✅:
  → ApprovedMarketsStore.approve() → writes to approved_markets.json
  → market_data.add_to_watchlist()  → live monitoring begins immediately

User taps ❌:
  → ApprovedMarketsStore.decline()  → writes market_id to declined list
  → never proposed again
```

### Watchlist commands

| Command | Behavior |
|---|---|
| `/watchlist` | Shows all pre-approved series + user-approved markets. Each user-approved market has an inline 🗑 Remove button. |

Tapping 🗑 Remove:
- Removes from `approved_markets.json`
- Removes from live watchlist immediately
- Market reverts to "unknown" — it may be proposed again on next discovery cycle.

### Persistence

`approved_markets.json` (project root):

```json
{
  "approved": [
    {
      "market_id": "KXBTC-26MAR3-00",
      "series_ticker": "KXBTC",
      "title": "BTC above $70,000 by end of week?",
      "exchange": "kalshi",
      "volume": 48200.0,
      "approved_at": "2026-03-31T11:00:00+00:00",
      "notes": ""
    }
  ],
  "declined": [
    "KXSOME-UNRELATED-MARKET-00"
  ]
}
```

### Adding a new pre-approved series

To permanently add a new Kalshi series (e.g. ETH 15-min) to the always-seed list,
add it to `ALWAYS_APPROVED_SERIES` in `bot/data/approved_markets.py`:

```python
ALWAYS_APPROVED_SERIES: dict[str, str] = {
    "KXBTC15M": "BTC 15-Minute Price Direction",
    "KXETH15M": "ETH 15-Minute Price Direction",   # ← add here
}
```

Then register the corresponding `MarketConfig` in `bot/main.py` under the
`--day-trader` block, mirroring how `BTC15MConfig` is registered.

### Key files

| File | Role |
|---|---|
| `bot/data/approved_markets.py` | `ApprovedMarketsStore` — read/write approved_markets.json |
| `bot/data/market_data.py` | `auto_discover_markets()` — runs filter chain, calls proposal callback |
| `bot/telegram_bot/bot.py` | `propose_market_to_user()`, `/watchlist`, approval callbacks |
| `bot/core/engine.py` | `on_market_proposed` slot — wired to `tg.propose_market_to_user` |
| `approved_markets.json` | Runtime persistence (project root, created on first approval) |

---

## 10. Tag System Reference

### Intent tags (mutually exclusive — first one wins)

| Tag | Effect |
|---|---|
| `#learn` | Knowledge ingestion — YouTube URL required |
| `#news` | Force news/signal pipeline on any URL |

### Market tags (can combine with intent or each other)

| Tag | Resolves to | Notes |
|---|---|---|
| `#btc15m` | `KXBTC15M` | Pins active BTC 15-min contract |
| `#kxbtc15m` | `KXBTC15M` | Same as above |
| `#btc15` | `KXBTC15M` | Alias |
| `#btc` | Search hint | Broad BTC research |
| `#bitcoin` | Search hint | Alias |
| `#eth` | Search hint | Broad ETH research |
| `#crypto` | Search hint | Broad crypto research |
| `#fed` | Search hint | Federal Reserve topics |
| `#rates` | Search hint | Interest rate topics |
| `#cpi` | Search hint | Inflation topics |
| `#macro` | Search hint | Macro topics |

### URL-only series (plain text blocked)

`KXBTC15M`, `KXETH15M` — 15-minute markets resolve too fast for text-based analysis.

---

## 11. Running the Bot

### Prerequisites

```bash
pip install python-telegram-bot beautifulsoup4 lxml httpx anthropic \
            youtube-transcript-api youtube-search-python \
            python-kalshi-api uvicorn fastapi
```

Node.js is required for the dashboard. It is managed via **nvm** — the `start.sh`
script resolves it automatically; no manual `nvm use` needed. For LTS vs Homebrew
pinning and GSD, see `docs/node-lts-macos.md`.

### Single launch command (recommended)

```bash
cd /Users/koojbot/maxthetrader
./start.sh           # paper mode (default)
./start.sh live      # live trading mode
```

`start.sh` starts everything in one shot:
- Trading engine (Kalshi, paper by default)
- Telegram bot
- REST API on port 8000
- Day trader pipeline (BTC 15-min)
- Next.js dashboard on port 3000 (background process)

**Ctrl-C** shuts down both the bot and the dashboard cleanly.

Dashboard URLs once running:
| URL | Purpose |
|---|---|
| `http://localhost:3000` | Portfolio overview, positions, trades, signals |
| `http://localhost:3000/logs` | 🖥️ Live Logs — real-time filterable bot output |

### Manual commands (individual components)

```bash
# Full stack manually
python3 -m bot.main --exchange kalshi --mode paper --telegram --api --day-trader

# Dashboard only (requires nvm node in PATH)
cd dashboard && npm run dev

# API-only (for dashboard development without the trading engine)
python3 -m bot.main --api-only
```

### Kill stuck port
```bash
kill -9 $(lsof -ti:8000)   # API port
kill -9 $(lsof -ti:3000)   # Dashboard port
```

### Telegram Setup
1. Message `@BotFather` → `/newbot` → copy token → add to `.env` as `TELEGRAM_BOT_TOKEN`
2. Message `@userinfobot` → copy numeric ID → add to `.env` as `TELEGRAM_ALLOWED_USER_IDS`

### First knowledge ingestion
```
#learn https://youtube.com/watch?v=<video_id>
```
Then check `/knowledge` to see what was filed, or watch the **Knowledge** filter tab
in the Live Logs dashboard.

---

## 12. File Index

### New files created

| File | Purpose |
|---|---|
| `bot/telegram_bot/bot.py` | Telegram bot handler, all commands + message routing |
| `bot/telegram_bot/content_fetcher.py` | og:description extractor + video transcript integration |
| `bot/telegram_bot/video_fetcher.py` | YouTube transcript extraction + cross-platform search |
| `bot/telegram_bot/tag_parser.py` | #tag parsing (intent + market tags) |
| `bot/agents/base.py` | Agent ABC + AgentResult dataclass |
| `bot/agents/pipeline.py` | Researcher → Synthesizer → Trader orchestrator |
| `bot/agents/researcher.py` | News + market search agent (Claude tool use) |
| `bot/agents/synthesizer.py` | Trading thesis generator (pure Claude reasoning) |
| `bot/agents/trader.py` | Signal generator (deterministic) |
| `bot/agents/day_trader_pipeline.py` | Day trader orchestrator with market config registry |
| `bot/agents/day_trader_researcher.py` | Autonomous market scanner with KB context injection |
| `bot/agents/day_trader.py` | Day trade execution agent |
| `bot/agents/knowledge_learner.py` | YouTube/Reddit transcript → KB cross-analysis agent |
| `bot/strategies/market_configs/base.py` | MarketConfig ABC + EntryWindow/FeeModel/PositionSizingRules |
| `bot/strategies/market_configs/btc15m.py` | BTC 15-min specific config (5-filter signal logic) |
| `bot/data/btc_price.py` | Live BTC price feed (Coinbase + Kraken fallback) |
| `bot/data/approved_markets.py` | `ApprovedMarketsStore` — three-tier watchlist with JSON persistence |
| `bot/data/source_scraper.py` | Platform-aware source detection + batch post fetching (all platforms) |
| `bot/data/subreddit_scraper.py` | Reddit-specific top posts fetcher |
| `bot/data/relevant_subreddits.py` | Subreddit registry → knowledge track mapping |
| `bot/utils/log_broadcaster.py` | WebSocket log broadcaster — 500-line ring buffer, streams to dashboard |
| `bot/knowledge/__init__.py` | Knowledge package init |
| `bot/knowledge/categories.py` | Track + agent routing definitions (`detect_track`, `detect_target_agent`) |
| `bot/knowledge/knowledge_base.py` | Agent-scoped KB persistence under `knowledge_store/agents/<agent_id>/<track>/` |
| `approved_markets.json` | Runtime watchlist state (project root, created on first approval) |
| `knowledge_store/agents/` | Runtime JSON for each agent's isolated knowledge categories |
| `dashboard/src/app/logs/page.tsx` | 🖥️ Live Logs dashboard tab — WebSocket, color-coded, filterable |
| `docs/KNOWLEDGE_INGESTION_STATUS.md` | Full status doc for knowledge ingestion system (session reference) |
| `start.sh` | Single launch script — bot + dashboard + all flags in one command |
| `logs/` | Log file directory |

### Modified files

| File | Key changes |
|---|---|
| `bot/main.py` | Added `--telegram`, `--day-trader` flags; DayTraderPipeline initialization; `ApprovedMarketsStore` wiring |
| `bot/core/engine.py` | Day trader pipeline integration, series reseeding every 30 cycles, `on_market_proposed` callback slot |
| `bot/data/market_data.py` | `seed_series_market()`, `get_series_market_id()`, series contract rotation; post-filter + approval callback in `auto_discover_markets()` |
| `bot/telegram_bot/bot.py` | Agent-targeted KB routing, per-agent `/knowledge` summaries, source ingest callbacks |
| `bot/telegram_bot/content_fetcher.py` | `FetchedContent.subreddit` field; `_resolve_reddit_share_link()` for `/s/` URLs; Reddit JSON → Firecrawl fallback |
| `bot/agents/knowledge_learner.py` | Agent-target resolution + agent-scoped dedupe/context/update writes |
| `bot/agents/pipeline.py` | `_md()` MarkdownV2 escape function applied to all Telegram output |
| `bot/utils/logger.py` | Added `BroadcastHandler` — captures every log line as JSON, streams to WebSocket clients |
| `bot/api/server.py` | Added `GET /api/telegram/inputs`; added `WebSocket /ws/logs` endpoint for live log streaming |
| `bot/utils/db.py` | Added `agent_inputs` table, `log_agent_input()`, `get_agent_inputs()` |
| `bot/config/settings.py` | Added `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` |
| `dashboard/src/app/layout.tsx` | Added 🖥️ Live Logs to sidebar navigation |
| `dashboard/src/app/globals.css` | Theme variables used across all dashboard pages |

---

## 13. Session 7 Multi-Agent Runtime

Session 7 introduces a true multi-agent execution model while preserving the existing BTC15m flow.

### Runtime registry

- New file: `bot/core/agent_registry.py`
- `TradingAgentSpec` defines runtime metadata (`agent_id`, `market_type`, `exchange`, `timeframe`, `enabled`, `runner`)
- `AgentQualityGate` defines promotion thresholds per agent

Current registered agents:

| Agent ID | Runner | Purpose |
|---|---|---|
| `btc15m_kalshi` | `day_trader` | Existing BTC15m specialist execution path |
| `polymarket_news` | `polymarket_news` | News/momentum conviction routed to Polymarket candidates |
| `stocks_etf_paper_shell` | `stocks_etf_shell` | Paper-only scaffold for future stocks/ETF trader |

### Engine routing changes (`bot/core/engine.py`)

- Added registry-driven `_run_registered_agents()` orchestration
- Added Polymarket candidate cycle with guardrails:
  - liquidity floor
  - spread ceiling
  - minimum time-to-close
- Added exchange-aware execution routing (`_get_exchange_for_signal`) so signals execute against intended exchange
- Added per-signal attribution (`agent_id`, `execution_exchange`)

### Signal and attribution changes

- `bot/core/signals.py`: `Signal` now includes:
  - `agent_id`
  - `execution_exchange`
- `bot/agents/trader.py`: removed exchange hardcoding and now uses inferred exchange context
- `bot/agents/day_trader.py`: day-trader signals emit `agent_id="btc15m_kalshi"`

### Risk model updates

- `bot/risk/manager.py` now applies exchange-specific risk profile overrides for Polymarket signals
- Supports tighter caps via:
  - `RISK_POLY_MAX_POSITION_PCT`
  - `RISK_POLY_MAX_EXPOSURE_PCT`

### API and dashboard visibility

- API:
  - `GET /api/agents/registry`
  - `GET /api/agents/quality-gates`
  - `agent_id` filters for signals/trades/telegram inputs
- Dashboard:
  - Signals page shows `agent_id`
  - Trades page shows `agent_id`
  - API client types updated to include attribution and registry/gate endpoints

### Quality gates documentation

Go/no-go criteria and default thresholds are tracked in:
- `docs/AGENT_QUALITY_GATES.md`
