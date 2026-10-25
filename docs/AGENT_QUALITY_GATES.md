# Agent Quality Gates

This document defines go/no-go gates for promoting a trading agent from scaffold to trusted runtime.

## Gate Framework

Each agent must pass all five gates before live promotion:

1. Data sanity
2. Signal quality
3. Risk behavior
4. Operational stability
5. Economic viability

## Current Runtime Thresholds

These thresholds are wired in `bot/main.py` and exposed via `GET /api/agents/quality-gates`.

| agent_id | min_signal_confidence | max_daily_drawdown_pct | min_sample_size | min_win_rate | min_net_edge_pct |
|---|---:|---:|---:|---:|---:|
| `btc15m_kalshi` | 0.55 | 0.03 | 30 | 0.47 | 0.02 |
| `polymarket_news` | 0.60 | 0.025 | 40 | 0.50 | 0.03 |
| `polymarket_copy` | 0.00 | 0.025 | 40 | 0.50 | 0.02 |
| `stocks_etf_paper_shell` | 0.00 | 0.00 | 0 | 0.00 | 0.00 |

## Verification Checklist

Use this checklist for every new agent:

- Data sanity: valid market metadata, non-zero prices, and bounded spread.
- Signal quality: confidence distribution and hit rate stable over sample window.
- Risk behavior: no position/exposure limit violations and predictable exits.
- Operational stability: no crash loops, repeated exceptions, or API regressions.
- Economic viability: positive net edge after fees/slippage in paper replays.
