# Knowledge Ingestion System — Status & Reference
> Last updated: 2026-04-10 (Session 7)
> **⚡ ACTIVE REFERENCE — Start here in Session 7+**
> Covers: agent-targeted knowledge routing, per-agent KB storage, source scraping, quality scoring, autonomous ingest, link following, known bugs, current KB state, and pending work.
>
> **Session 6 starting point:** Work through Section 7 (What Needs to Happen Next) in order.
> The first task is to restart the bot and test the autonomous quality-scored ingest flow end-to-end.

---

## 1. What This System Does

When the user sends the bot any social media post (Reddit, YouTube, Instagram, TikTok, X/Twitter, LinkedIn), the bot:

1. **Fetches** the full content of the post
2. **Follows embedded links** — if the post body contains a YouTube URL, extracts the transcript and appends it to the content
3. **Auto-classifies** it as either `knowledge` (strategy/educational) or `signal` (news/catalyst)
4. If `knowledge` → runs it through the **KnowledgeLearnerAgent**, which uses Claude to extract structured trading rules and stores them in the knowledge base
5. **Identifies the source** (subreddit, YouTube channel, Instagram profile, etc.)
6. **Autonomously batch-ingests** quality content from the same source — no user approval needed. Quality scoring filters out noise; only posts with sufficient engagement/depth are ingested.

The knowledge base feeds back into the live trading engine: before every trade decision the `DayTraderResearcher` injects relevant KB rules directly into its prompt.

---

## 2. Agent-Targeted Knowledge Architecture

### The Problem
Traditional day trading concepts (FVG, liquidity grabs, 4H chart structure, moving averages) do NOT apply to Kalshi 15-minute BTC prediction markets. Kalshi is a binary bet on crowd pricing — the "edge" comes from detecting market mispricing, not chart patterns. A single pooled KB causes cross-contamination and weak specialization.

### The Solution: Per-Agent KB + Track-Aware Categories

```
knowledge_store/
  agents/
    btc15m_kalshi/
      kalshi/
        crowd_pricing.json
        btc_signal_relevance.json
        contract_mechanics.json
        edge_identification.json
        kalshi_risk.json
    polymarket_news/
      kalshi/
        crowd_pricing.json
        btc_signal_relevance.json
        contract_mechanics.json
        edge_identification.json
        kalshi_risk.json
    stocks_etf_paper_shell/
      traditional/
        entry_setup.json
        exit_management.json
        market_structure.json
        indicator_usage.json
        risk_management.json
        market_timing.json
        btc_specific.json
```

### Agent Target Resolver (`bot/knowledge/categories.py`)

`detect_target_agent(text, subreddit="", hint="auto")` routes learning into:

- `btc15m_kalshi` for Kalshi/BTC15m-specific context
- `polymarket_news` for Polymarket-specific context
- `stocks_etf_paper_shell` for traditional stocks/ETF-style context

The resolver supports:
- **automatic routing** from content/subreddit signals
- **legacy track hints** (`kalshi`, `traditional`)
- **explicit target hints** (agent IDs)

### Practical Tagging Guidance

Use these tags with `#learn` when you want explicit targeting:

- `#btc15m` → force BTC15m Kalshi specialist
- `#polymarket` → force Polymarket specialist
- `#stocks` / `#etf` / `#equities` → force stocks/ETF specialist

### Track Categories

**Traditional (7 categories):**
| Category | Covers |
|---|---|
| `entry_setup` | Entry trigger conditions, confirmation signals, timing |
| `exit_management` | Stop loss, take profit, trailing stops, time exits |
| `market_structure` | Support/resistance, chart patterns, trends, price action |
| `indicator_usage` | RSI, MACD, Bollinger Bands, volume, moving averages |
| `risk_management` | Position sizing, max loss limits, capital preservation |
| `market_timing` | Session timing, when to sit out, news windows |
| `btc_specific` | Funding rates, liquidations, on-chain signals, cycle effects |

**Kalshi (5 categories):**
| Category | Covers |
|---|---|
| `crowd_pricing` | How the crowd prices YES/NO, typical mispricing patterns |
| `btc_signal_relevance` | Which BTC signals actually matter for 15-min window bets |
| `contract_mechanics` | CF Benchmarks BRTI resolution, fee math, contract behavior |
| `edge_identification` | When the market is wrong and how to spot it |
| `kalshi_risk` | Kalshi-specific risk: position limits, max exposure, drawdown |

### Track Detection Logic (`bot/knowledge/categories.py`)

`detect_track(text, subreddit="")` — three-step decision:

1. **Subreddit name check (highest priority):** If the post came from a Reddit subreddit, the subreddit name is used as the primary signal. `kalshi` keyword in name → `"kalshi"` track. `trading`/`btc` keywords → `"traditional"`. This overrides content scoring.
2. **Content keyword scoring (fallback):** Sum keyword hits against both category sets. Kalshi wins only with a clear margin.
3. **Default:** Ambiguous content routes to `"traditional"`.

**Why subreddit takes priority:** A Kalshi-specific post (e.g., "+20¢ take-profit on BTC binary") uses traditional trading vocabulary (stop loss, candlestick, indicator) that scores 7 traditional hits vs 4 Kalshi hits. Without the subreddit hint, it would be misclassified.

---

## 3. Source Scraping & Autonomous Ingest Architecture

### The Flow (Updated Session 5 — Autonomous Quality-Scored Ingest)

```
User sends post URL
        │
        ▼
fetch_social_content()          content_fetcher.py
  ├── Reddit  → JSON API (full post body + top comments)
  │              + canonical_url resolved for dedup
  │              + embedded_urls extracted from body
  ├── YouTube → oEmbed + transcript extraction
  ├── Instagram/TikTok → og:description + YouTube cross-search
  └── X/Twitter/LinkedIn → Firecrawl / og tags
        │
        ▼ FetchedContent (includes .subreddit, .channel_name,
        │                  .canonical_url, .embedded_urls)
        │
        ▼
Link following: if embedded_urls contains YouTube → extract transcript
  └── Appended as [EMBEDDED VIDEO TRANSCRIPT] to content
        │
        ▼
_auto_classify()                "knowledge" or "signal"
        │
  route == "knowledge"
        │
        ▼
_handle_learn_content(
    content, url, platform,
    force_track="auto",
    subreddit=fetched.subreddit
)
        │
        ▼
KnowledgeLearnerAgent.learn(
    ..., track="auto",   # hint/override
    subreddit="KalshiBTCUporDown15"
)
        │
        ▼
detect_target_agent() → "btc15m_kalshi"
        │
        ▼
knowledge_base.apply_updates(track="kalshi", agent_id="btc15m_kalshi")
  └── writes to knowledge_store/agents/btc15m_kalshi/kalshi/*.json
        │
        ▼
_maybe_propose_source(update, fetched)     ← AUTONOMOUS (no buttons)
        │
        ▼
detect_source(platform, url, subreddit=...)
  └── Reddit → SourceInfo (scrape_supported=True)
        │
        ▼
fetch_source_top_posts(source, limit=25, min_score=1)
        │
        ▼
score_and_filter_posts(posts)              ← QUALITY SCORING
  ├── compute_quality() per post
  ├── Hard filter: skip empty posts (body<50 + 0 comments)
  ├── Hard filter: skip meta/question titles (unless q>20)
  ├── Quality floor: skip q < 5.0
  └── Cap at AUTO_INGEST_MAX_POSTS (10)
        │
        ▼
Informational Telegram message (what passed/failed)
        │
        ▼
asyncio.create_task(_run_source_ingest(..., pre_filtered_posts=...))
  For each quality post (with 1.5s sleep between):
    1. Dedup check (has_processed_video? using canonical URL)
    2. _fetch_reddit_post(post.url)
    3. Link following: check for embedded YouTube → extract transcript
    4. KnowledgeLearnerAgent.learn(track=track)
    5. Accumulate new_rules, confirmations, contradictions
  └── Final Telegram summary
```

### Quality Scoring Engine (`bot/data/source_scraper.py`)

```python
quality = (num_comments × 3) + (body_length / 100) + (has_embedded_media × 10)
```

| Config Constant | Value | Purpose |
|---|---|---|
| `QUALITY_COMMENT_WEIGHT` | 3 | Multiplier for num_comments |
| `QUALITY_BODY_DIVISOR` | 100 | body_length / this value |
| `QUALITY_MEDIA_BONUS` | 10 | Flat bonus for embedded media (YouTube links) |
| `QUALITY_FLOOR` | 5.0 | Minimum quality_score to include a post |
| `AUTO_INGEST_MAX_POSTS` | 10 | Cap on posts per autonomous ingest |
| `_SKIP_TITLE_OVERRIDE` | 20.0 | Skip-title posts above this quality still pass |

**Hard filters (applied before quality floor):**
- **Empty posts:** body_length < 50 AND num_comments == 0 → skip
- **Meta/question titles:** Regex matches welcome, meta, megathread, "what/who/how/does anyone..." → skip (unless q > 20)

**Example from r/KalshiBTCUporDown15 (10 raw → 7 quality):**
- PASS: "My Current Strategy" (q=107.3, 28 comments)
- PASS: "My full strategy for BTC up to down 15" (q=57.3, 15 comments)
- SKIP: "👋 Welcome to r/KalshiBTCUporDown15!" (title filter)
- SKIP: "What strategies do you use?" (title filter)
- SKIP: "Does anyone else..." (title filter)

### Platform Support Matrix

| Platform | Source Detection | Batch Scrape | Ranking Used | Notes |
|---|---|---|---|---|
| Reddit | ✅ Subreddit name | ✅ Full | Upvotes | Only fully-supported platform |
| YouTube | ✅ Channel name/URL | ❌ Blocked | Views | `youtubesearchpython` broken (httpx `proxies` removed) |
| Instagram | ✅ Username from URL | ❌ Blocked | Likes + comments | Login wall, no public API |
| TikTok | ✅ Username from URL | ❌ Blocked | Likes + views | Bot detection |
| X/Twitter | ✅ Handle from URL | ❌ Blocked | Likes + retweets | Requires paid API ($100+/mo) |
| LinkedIn | ✅ Profile/company | ❌ Limited | Reactions | Firecrawl credits, rate limited |

### Key Files

| File | Role |
|---|---|
| `bot/data/source_scraper.py` | `SourceInfo` + `SourcePost` dataclasses, `detect_source()`, `fetch_source_top_posts()`, `compute_quality()`, `score_and_filter_posts()` |
| `bot/data/subreddit_scraper.py` | Legacy Reddit-only scraper (superseded by source_scraper) |
| `bot/data/relevant_subreddits.py` | Registry of known relevant subreddits → track mapping |
| `bot/knowledge/categories.py` | Category definitions for both tracks + `detect_track()` |
| `bot/knowledge/knowledge_base.py` | Persistence layer — track-aware load/save/apply_updates |
| `bot/agents/knowledge_learner.py` | KnowledgeLearnerAgent — Claude-powered rule extraction |
| `bot/telegram_bot/content_fetcher.py` | `FetchedContent` with `canonical_url`, `embedded_urls`, URL extraction |
| `bot/telegram_bot/video_fetcher.py` | YouTube transcript extraction (updated for v1.2.4 API) |

---

## 4. Link Following (Session 5 Addition)

When a Reddit post contains a YouTube URL in its body, the bot:

1. Scans `FetchedContent.embedded_urls` for YouTube video IDs
2. Extracts the transcript via `youtube-transcript-api` v1.2.4
3. Appends the transcript as `[EMBEDDED VIDEO TRANSCRIPT]` to the content before knowledge extraction
4. The KnowledgeLearnerAgent processes both the post text AND the video transcript together

This is critical for subreddits like r/KalshiBTCUporDown15 where posts frequently link to strategy walkthrough videos.

**YouTube transcript API (v1.2.4):**
```python
# Instance method, not class method
api = YouTubeTranscriptApi()
fetched_transcript = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
raw = " ".join(seg.text.strip() for seg in fetched_transcript)
```

Link following is wrapped in try/except — if transcript extraction fails, the bot notifies the user ("processing post text only") and continues with the post body alone.

---

## 5. Current Knowledge Base State (as of 2026-04-04)

### Kalshi KB (`knowledge_store/kalshi/`) — 10 rules

| Category | Rules | Key Rules |
|---|---|---|
| `edge_identification` | 5 | Big moves → choppy; exit after first win; contrarian YES buys at 30-40¢; sit out bad conditions; focus on sure things |
| `btc_signal_relevance` | 3 | RSI<30 oversold; wide Bollinger Bands = low predictability; ADX 20s=choppy vs 30s=directional |
| `crowd_pricing` | 1 | Bot-driven pricing makes probabilities fairly accurate |
| `contract_mechanics` | 1 | +20¢ take-profit on limit orders |
| `kalshi_risk` | 0 | Not yet populated |

**Source:** Single Reddit post from r/KalshiBTCUporDown15 + embedded YouTube transcript (4,035 chars)

### Traditional KB (`knowledge_store/traditional/`) — 6 rules

| Category | Rules | Source |
|---|---|---|
| `entry_setup` | 3 | r/daytrade (FVG strategy, liquidity grab entry, limit in gap) |
| `market_structure` | 1 | r/daytrade (4H range definition) |
| `risk_management` | 2 | r/daytrade (backtest first, R:R over perfection) |
| `exit_management` | 0 | Cleaned (was misclassified Kalshi content) |
| `market_timing` | 0 | Cleaned (was misclassified Kalshi content) |
| `btc_specific` | 0 | Cleaned (was misclassified Kalshi content) |
| `indicator_usage` | 0 | Not yet populated |

---

## 6. Bugs Fixed in Session 5

### Bug 1: YouTube transcript API broken ✅ FIXED
**What:** `YouTubeTranscriptApi.get_transcript()` → `AttributeError` (v1.2.4 changed to instance method `.fetch()`, segments changed from `seg["text"]` to `seg.text`)
**Fix:** `bot/telegram_bot/video_fetcher.py` — `api = YouTubeTranscriptApi(); api.fetch(...)` + `seg.text`

### Bug 2: Reddit share link deduplication ✅ FIXED
**What:** Reddit mobile share links (`/r/sub/s/XXXXX`) generate unique URLs per share; same post processed 3 times.
**Fix:** `bot/telegram_bot/content_fetcher.py` — Added `canonical_url` field to `FetchedContent`, resolved before dedup check.

### Bug 3: No log files written ✅ FIXED
**What:** `setup_logger()` only had console + WebSocket handlers, no file output.
**Fix:** `bot/utils/logger.py` — Added `FileHandler` writing to `logs/bot_YYYYMMDD_HHMM.log`.

### Bug 4: WebSocket RuntimeError on disconnect ✅ FIXED
**What:** `receive_text()` raises `RuntimeError` on abrupt disconnect, only `WebSocketDisconnect` was caught.
**Fix:** `bot/api/server.py` — `except (WebSocketDisconnect, RuntimeError):`

### Bug 5: Bot process detection in start.sh ✅ FIXED
**What:** `ps aux | grep python3` returned nothing because macOS system Python runs as `Python` (capital P).
**Fix:** `start.sh` — Changed to `pgrep -f "bot.main"` which matches on command arguments.

### Bug 6: Silent failure on YouTube transcript error ✅ FIXED
**What:** Link-following block had no try/except; transcript failure killed the handler silently.
**Fix:** Wrapped in try/except, added Telegram notification "processing post text only".

### Bug 7: Batch ingest returning 0 despite proposal showing 10 ✅ FIXED
**What:** `_maybe_propose_source_inner()` used `min_score=1` but `_run_source_ingest()` re-fetched with `min_score=5`.
**Fix:** Changed `_run_source_ingest()` to `min_score=1`.

### Bug 8: Quality filter not catching emoji-prefixed titles ✅ FIXED
**What:** `_SKIP_TITLE_RE` anchored to `^\s*` which didn't match emoji `👋` prefix.
**Fix:** Changed anchor to `^[^\w]*` and made "welcome"/"share yours" non-anchored patterns.

---

## 7. What Needs to Happen Next (Session 6)

### 7a. Restart bot and test autonomous ingest end-to-end ← IMMEDIATE NEXT STEP
All Session 5 code changes are written but the bot needs a restart to pick them up.
1. Kill any running bot: `kill $(pgrep -f 'bot.main')`
2. Start fresh: `./start.sh`
3. Send a **NEW** Reddit post (the test post's canonical URL is already in KB — dedup will skip it)
4. Verify full flow: fetch → link follow → learn → auto-score source → ingest quality posts
5. Check that quality scoring filters appear in logs
6. Verify no approval buttons sent — ingest should be fully autonomous

### 7b. Populate more Kalshi KB rules
6 quality posts from r/KalshiBTCUporDown15 remain un-ingested from the auto-ingest.
Send new posts from other Kalshi-related subreddits or YouTube channels.
Goal: 20-30 rules across all 5 Kalshi categories.

### 7c. YouTube batch scraping
Still blocked — `youtubesearchpython` library broken with recent httpx. Options:
- Add a YouTube Data API key to `.env` and use direct API calls
- Replace library with `yt-dlp` metadata extraction
- Continue with manual individual video URL sends

### 7d. Connect Kalshi KB to live trading decisions
`format_relevant_context(track="kalshi")` is already wired into `DayTraderResearcher`.
Auto-works once KB is populated — should already be active with the 10 existing rules.

### 7e. Clean up dead code
`_cb_sr_ingest` and `_cb_sr_skip` callback handlers in bot.py are dead code (approval buttons no longer sent). Remove or mark deprecated.

### 7f. Update ARCHITECTURE.md
Needs Session 5 additions: link following, quality scoring, autonomous ingest, logger file handler, content_fetcher canonical_url/embedded_urls.

---

## 8. Architecture Changes Made in Sessions 4-5

### Session 5 new/changed files

| File | What Changed |
|---|---|
| `bot/utils/logger.py` | Added FileHandler for `logs/bot_YYYYMMDD_HHMM.log` |
| `bot/api/server.py` | WebSocket error handling: `except (WebSocketDisconnect, RuntimeError)` |
| `start.sh` | Process guard via `pgrep -f "bot.main"` |
| `bot/telegram_bot/content_fetcher.py` | `FetchedContent.canonical_url`, `.embedded_urls`, `_extract_embedded_urls()` |
| `bot/telegram_bot/video_fetcher.py` | YouTube API v1.2.4 migration (instance method + `.text` segments) |
| `bot/telegram_bot/bot.py` | Link following in `_handle_url()`, autonomous ingest in `_maybe_propose_source_inner()`, pre-filtered posts in `_run_source_ingest()` |
| `bot/data/source_scraper.py` | Quality scoring: `compute_quality()`, `score_and_filter_posts()`, `_SKIP_TITLE_RE`, engagement fields on `SourcePost` |

### Session 4 new files (for reference)
| File | Purpose |
|---|---|
| `bot/data/source_scraper.py` | Platform-aware source detection + batch post fetching |
| `bot/data/subreddit_scraper.py` | Reddit-specific top posts fetcher (superseded by source_scraper) |
| `bot/data/relevant_subreddits.py` | Subreddit registry → knowledge track mapping |
| `knowledge_store/traditional/` | Track subdirectory for traditional trading knowledge |
| `knowledge_store/kalshi/` | Track subdirectory for Kalshi prediction market knowledge |

---

## 9. Live Logs Dashboard & Launch Script

### `./start.sh` — single launch command
```bash
./start.sh           # paper mode (default)
./start.sh live      # live trading
```
Starts: bot engine + Telegram + REST API + day trader pipeline + Next.js dashboard.
Now includes process guard — exits with warning if bot.main is already running.

### WebSocket log streaming
- `bot/utils/log_broadcaster.py` — 500-entry ring buffer, catch-up on connect
- `bot/utils/logger.py` — `BroadcastHandler` strips Rich markup, formats as JSON
- `bot/api/server.py` — `/ws/logs` WebSocket endpoint
- `dashboard/src/app/logs/page.tsx` — Browser UI with 5 filter tabs + color coding

### Log file output (Session 5)
- `logs/bot_YYYYMMDD_HHMM.log` — plain text, `HH:MM:SS  LEVEL     message` format
- New file created per bot restart
- Can verify clean restart by checking timestamp of newest log file
