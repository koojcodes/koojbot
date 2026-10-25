# MaxTheTrader Test-Ready Checklist

This checklist defines the first milestone for beginning systematic testing.

## Official milestone-1 runtime path

- Boot path: `./start.sh`
- Mode: paper
- Required surfaces:
  - trading engine
  - FastAPI API
  - Next.js dashboard
  - Telegram bot
- Optional for milestone-1 pass:
  - swarm/graph endpoints when `SWARM_ENABLED=false`
  - live Polymarket order execution
  - stocks/ETF scaffold execution

## Startup readiness

- `.env` exists at project root
- `venv` is available
- `logs/` can be created
- port `8000` is free before startup
- port `3000` is free before dashboard startup
- `bot.main` is not already running

## Minimum environment expectations

- `KALSHI_API_KEY`
- `KALSHI_PRIVATE_KEY_PATH`
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `PAPER_MODE=true`

Recommended milestone-1 toggles:

- `AGENT_ENABLE_BTC15M=true`
- `AGENT_ENABLE_POLYMARKET_NEWS=false`
- `AGENT_ENABLE_POLYMARKET_COPY=false`
- `AGENT_ENABLE_STOCKS_ETF_SHELL=false`
- `SWARM_ENABLED=false`

These defaults keep the startup surface focused on the core stack. Enable other systems intentionally while expanding test coverage.

## API-only smoke test

Run:

```bash
python3 -m bot.main --api-only
python3 scripts/validate_test_readiness.py
```

Expected:

- `/api/health` returns `status=ok`
- `/api/portfolio` returns a stable empty or snapshot-backed object
- `/api/portfolio/history` returns JSON
- `/api/agents/registry` returns JSON
- `/api/orchestration/status` returns a stable payload even when engine is not running

## Full-stack smoke test

Run:

```bash
./start.sh
```

Expected:

- dashboard starts on `http://localhost:3000`
- API starts on `http://localhost:8000`
- Telegram bot starts polling without fatal exit
- day trader pipeline initializes
- no immediate uncaught exception loop

## Dashboard acceptance checks

- every nav item resolves to a real route
- overview loads when API is available
- agents page renders orchestration plus runtime registry data
- graph page surfaces disabled/error state explicitly
- simulations page surfaces history-loading failure explicitly
- markets page surfaces watchlist failure explicitly
- learning loop page surfaces API errors explicitly

## Telegram ingest readiness checks

Validate one fresh Reddit URL flow:

1. send a Reddit URL from an allowed Telegram account
2. confirm fetch and classification succeed
3. confirm the learner path writes state
4. confirm autonomous ingest announces quality-filtered follow-up posts
5. confirm duplicate content is skipped on replay

Validate one signal flow:

1. send a signal-style URL or `#news <url>`
2. confirm pipeline progress runs
3. confirm signal logging appears in DB/API

## Copy-trading readiness checks

Paper-mode validation only:

```bash
python3 scripts/validate_copy_setup.py
python3 scripts/run_paper_copy_validation.py
```

Expected:

- configured leader state is visible
- cursor initialization is non-fatal
- skip reasons are visible through `/api/copy/stats`
- mirrored trades, if any, are attributed to `polymarket_copy`

## DB observability checks

The following tables should be inspectable during milestone-1 testing:

- `portfolio_snapshots`
- `signals`
- `trades`
- `agent_inputs`
- `copy_trade_cursors`
- `copy_trade_seen`

## Done means

This milestone is complete when:

- the paper-mode stack is the documented official test path
- core startup blockers are removed or documented
- the dashboard is a trustworthy readout of backend state
- one ingest flow and one signal/copy validation flow are repeatable
- the smoke-test script passes for core endpoints
