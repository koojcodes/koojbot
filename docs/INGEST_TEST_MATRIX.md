# Telegram Ingest Test Matrix

This matrix defines the first reproducible ingest cases for milestone-1 testing.

## In-scope platforms

- Reddit URL with post body and comments
- direct YouTube URL
- generic web/news article URL
- free text with `#news`

## Deferred for later milestones

- batch YouTube channel scraping
- batch ingest from TikTok, Instagram, X, LinkedIn, or Facebook sources
- plain-text `#btc15m` cases for 15-minute markets

## Routing expectations

| Input type | Example | Expected route | Notes |
|---|---|---|---|
| Reddit URL | `https://reddit.com/r/.../comments/...` | auto-classified knowledge or signal | Best milestone-1 ingest source |
| YouTube URL with `#learn` | `#learn https://youtube.com/watch?v=...` | knowledge learner | Explicit override |
| URL with `#news` | `#news https://...` | signal pipeline | Explicit override |
| free text with `#news` | `#news BTC just reclaimed 70k` | signal pipeline | Useful for non-URL signal verification |
| free text with `#btc15m` | `#btc15m BTC looks strong` | blocked | Expected behavior |
| Reddit URL with embedded YouTube link | Reddit strategy post | knowledge learner + transcript append | Validates link following |

## First milestone validation cases

### Case 1: Reddit knowledge ingest

Goal:

- prove fetch -> classify -> learn -> autonomous source ingest works

Fetch notes:

- Preferred path: Reddit JSON API (`.json` suffix)
- If `reddit.com` returns 403/blocked from the runtime network, the fetcher falls back to Pullpush archive (`reddit_pullpush`)
- Firecrawl is a secondary fallback, but Firecrawl itself may also refuse Reddit URLs

Expected evidence:

- Telegram acknowledges processing
- KB update summary is returned
- source scoring announcement appears
- additional quality posts are processed automatically
- duplicate replay is skipped on a second send

Primary files:

- [bot/telegram_bot/bot.py](/Users/koojbot/maxthetrader/bot/telegram_bot/bot.py)
- [bot/telegram_bot/content_fetcher.py](/Users/koojbot/maxthetrader/bot/telegram_bot/content_fetcher.py)
- [bot/agents/knowledge_learner.py](/Users/koojbot/maxthetrader/bot/agents/knowledge_learner.py)
- [bot/data/source_scraper.py](/Users/koojbot/maxthetrader/bot/data/source_scraper.py)
- [bot/knowledge/knowledge_base.py](/Users/koojbot/maxthetrader/bot/knowledge/knowledge_base.py)

### Case 2: Explicit YouTube learn flow

Goal:

- prove direct transcript extraction and KB write path

Expected evidence:

- transcript fetch succeeds or degrades visibly
- learner writes updates
- processed-video dedup blocks replay

### Case 3: Explicit signal flow

Goal:

- prove Telegram content can enter the signal pipeline and surface in observability

Example:

```text
#news https://example.com/btc-breakout-article
```

Expected evidence:

- progress updates from Researcher -> Synthesizer -> Trader
- resulting signal recorded in `/api/signals`
- related row recorded in `agent_inputs`

### Case 4: Unsupported/deferred source behavior

Goal:

- prove blocked batch platforms fail explicitly rather than silently

Expected evidence:

- user-facing or log-visible explanation
- no broken approval-button path

## Assertions to verify

- tag parsing removes known tags and preserves clean text
- `#learn` forces knowledge mode
- `#news` forces signal mode
- URL-only short-horizon series stay blocked for plain text
- source-ingest callbacks are no longer required for autonomous ingest
- dedup prevents reprocessing of the same canonical content
- failures are visible in logs or user replies
