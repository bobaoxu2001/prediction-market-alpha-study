# prediction-market-alpha-study

This repository contains a **structural feasibility study** for trading Kalshi hourly Bitcoin contracts (`KXBTCD`).

The goal is not to build a complex alpha model, but to answer a practical question:
> Is there statistically meaningful and economically tradable edge after realistic costs?

---

## What this project does

The script `analysis/kalshi_btc_feasibility.py` runs an end-to-end pipeline:

1. Pulls Kalshi market structure metadata (series rules, settlement source, tick size, fee type).
2. Pulls settled `KXBTCD` markets and minute-level Kalshi candlesticks.
3. Pulls BTC 1-minute proxy data from Coinbase (`BTC-USD`).
4. Builds simple baseline features (momentum, volatility regime, lag checks).
5. Runs walk-forward out-of-sample tests with transaction costs.
6. Produces a structured feasibility report + machine-readable metrics.

---

## Key outputs

- `reports/feasibility_report.md`  
  Human-readable final report (market structure, data feasibility, costs, baseline performance, risk, recommendation).

- `data/processed/feasibility_results.json`  
  Raw metrics and assumptions used in the report.

- `data/processed/market_minute_data.csv` *(generated locally when running the script; usually not committed)*  
  Intermediate minute-level market dataset.

---

## What useful result came out?

**Yes — a concrete decision came out: current setup is NO-GO.**

From the generated results in this repo:

- Data is usable for baseline research (historical settled markets + minute candles are available).
- But under realistic short-horizon execution assumptions, break-even win rates were above 100% in tested scenarios (structural cost hurdle).
- Baseline OOS models (logistic / momentum / vol-regime) were all negative after costs.

So this produced a useful output: **not just code, but a falsifiable go/no-go conclusion with evidence**.

---

## Module-by-module review (逐模块复盘 + 改进建议)

Below is a direct review of each study module, including what worked, what can be improved, and what currently blocks viability.

### Phase 1 - Market Structure Analysis

**Current status**
- Contract structure, settlement source, tick size, and API fields are verified from live Kalshi API + contract docs.
- Maker/taker distinction is confirmed at API model level (`maker_fees`, `taker_fees` fields).

**Can improve**
- Add an automated daily snapshot job for `rules_primary`, tick size, fee type, and position limits to detect rule drift.
- Parse and archive fee schedule details in a machine-readable form once accessible.

**Constraint / risk**
- Documentation drift exists (older filing text vs current live rule text).
- Fee schedule PDF may be temporarily blocked by anti-bot checks from cloud environments.

---

### Phase 2 - Data Feasibility

**Current status**
- Study used large sample slices (e.g., settled markets, minute-level candles, joined BTC minute proxy).
- Public data is enough for baseline validation.

**Can improve**
- Replace Coinbase proxy with direct settlement-aligned source (BRTI or closest available feed) to reduce basis mismatch.
- Add authenticated historical fills/orders if account permissions are available.

**Constraint / risk**
- No reliable public historical full-depth orderbook snapshots for queue modeling.
- Without queue + fill-probability history, maker-execution backtests are incomplete.

---

### Phase 3 - Transaction Cost Modeling

**Current status**
- Costs include spread + slippage + round-trip fee assumptions.
- Break-even table shows severe cost pressure.

**Can improve**
- Add scenario grid by holding period (1m/5m/15m/30m) instead of only next-minute exits.
- Split taker-only vs maker-first execution assumptions.

**Constraint / primary bottleneck**
- In current short-horizon taker-style assumptions, break-even win rates are above 100% in tested scenarios.
- This is the main blocker: **costs are larger than expected edge magnitude**.

---

### Phase 4 - Baseline Signal Testing

**Current status**
- Walk-forward OOS tests were implemented with no look-ahead.
- Logistic/momentum/vol-regime baselines all negative after costs.

**Can improve**
- Add confidence gating (trade only when expected move > cost hurdle).
- Add event-time features (time-to-close) and cross-strike context features.

**Constraint / risk**
- Statistical correlation exists, but monetizable edge is weak once costs are applied.
- H3 indicates strongest relation at lag 0, so delayed-reaction alpha is limited.

---

### Phase 5 - Risk & Scalability

**Current status**
- Key risks identified: liquidity concentration, slippage sensitivity, regime dependence, tail risk near close.

**Can improve**
- Add explicit capacity simulation by hour/strike/liquidity buckets.
- Stress-test with wider slippage tails and quote-gap events.

**Constraint / risk**
- Capacity is likely limited to the most liquid strikes/hours.
- Binary payout + near-close gaps create non-linear downside if exits fail.

---

## If final conclusion is NO-GO, where is the bottleneck?

### Core bottleneck stack (from strongest to weaker)
1. **Microstructure cost wall (strongest):** spread + slippage + fee dominate next-minute expected move.
2. **Execution realism gap:** no queue-level historical depth => cannot validate maker alpha robustly.
3. **Low exploitable lag:** reaction appears mostly contemporaneous, leaving little clean delay edge.
4. **Proxy mismatch risk:** settlement uses BRTI while baseline used Coinbase BTC candles.

In short: **not “no signal at all”, but “signal too small vs execution frictions.”**

---

## Possible direction changes (换方向建议)

If continuing research, prioritize these pivots:

1. **Longer holding horizons first**  
   Re-test 5m/15m/30m exits; require expected move to exceed full cost hurdle before entry.

2. **Relative-value over directional trading**  
   Focus on cross-strike/event-curve inconsistencies (monotonic/no-arbitrage checks), not raw BTC direction.

3. **Maker-first execution research**  
   Only if historical fills/orders or live paper execution logs are available to estimate fill probability.

4. **Settlement-window specialization**  
   Build features around time-to-close and settlement mechanics (with settlement-aligned price feed).

5. **Market-selection filter**  
   Trade only high-liquidity slices with tighter spread and proven stable post-cost edge.

---

## Recommended go/no-go gates for next iteration

Do not move to production unless all are met:
- Cost-adjusted break-even win rate falls into feasible range (practically <=55%-60% for chosen setup).
- OOS Sharpe remains positive and stable across walk-forward windows.
- Positive returns in most subperiods (not one lucky regime).
- Capacity/slippage stress tests remain profitable under adverse assumptions.

---

## Reproduce

```bash
python3 analysis/kalshi_btc_feasibility.py
```

---

## Data sources

- Kalshi public API: `https://api.elections.kalshi.com/trade-api/v2`
- Kalshi docs: `https://docs.kalshi.com/`
- BTC proxy market data: Coinbase `BTC-USD` candles

---

## Notes and caveats

- The study is intentionally skeptical and prioritizes avoiding false positives.
- This is a feasibility validation, not a production strategy.
- Historical full-depth orderbook snapshots are not publicly exposed in the same way as top-of-book/candlestick data, which limits microstructure realism.
