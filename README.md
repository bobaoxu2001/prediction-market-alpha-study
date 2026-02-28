# prediction-market-alpha-study

This repository contains a **structural feasibility study** for trading Kalshi hourly Bitcoin contracts (`KXBTCD`).

The objective was deliberately narrow:
> Determine whether simple, out-of-sample signals can survive realistic execution costs in Kalshi hourly BTC markets.

This is a feasibility and market-structure validation project, not a production alpha system.

---

## Executive Summary (1-minute read)

- **Question:** Can simple, statistically grounded signals in Kalshi hourly BTC contracts be traded profitably after realistic frictions?
- **Approach:** Validate market structure and data availability, then run walk-forward out-of-sample baselines with spread/slippage/fee costs included.
- **Data used:** 35,687 settled markets scanned, 360 liquid markets selected, 51,445 Kalshi minute rows, 29,414 BTC proxy minute rows, 50,666 model-ready rows.
- **Key finding:** Statistical relationships exist, but they are too small relative to execution costs at the tested short horizon.
- **Cost reality:** Break-even win rates in tested scenarios were all above 100%, indicating a structural economic hurdle.
- **Model outcome:** Logistic, momentum, and volatility-regime baselines were all negative out-of-sample after costs.
- **Decision:** **NO-GO** for the tested setup.
- **Most likely next direction (if continuing):** longer holding horizons, relative-value/cross-strike structure, and better execution/fill modeling.

---

## TL;DR

- A complete end-to-end pipeline was implemented in `analysis/kalshi_btc_feasibility.py`.
- Market structure and data access were validated from live APIs and contract documents.
- Baseline models were tested with walk-forward validation and transaction costs.
- **Final conclusion: NO-GO under the tested short-horizon execution setup.**
- Main reason: **execution frictions (spread + slippage + fees) dominate expected edge**.

---

## What was completed

The following work was done in this repository:

1. **Market structure validation**
   - Queried Kalshi series/market metadata for `KXBTCD`.
   - Verified settlement source, tick size, fee type, and position-limit text.
   - Confirmed maker/taker fee fields exist in API models.

2. **Data feasibility validation**
   - Pulled settled hourly BTC contracts.
   - Pulled minute-level Kalshi market candlesticks.
   - Pulled minute-level BTC reference candles from Coinbase.
   - Confirmed what is and is not available publicly (e.g., no robust public historical full-depth order book snapshots).

3. **Cost-aware baseline testing**
   - Built simple features (momentum, volatility regime, lag structure).
   - Implemented walk-forward out-of-sample evaluation.
   - Included transaction costs (spread, slippage, round-trip fees).
   - Measured PnL, win rate, Sharpe, drawdown, and break-even requirements.

4. **Deliverables generated**
   - Human-readable report: `reports/feasibility_report.md`
   - Machine-readable metrics: `data/processed/feasibility_results.json`
   - Optional intermediate dataset (local): `data/processed/market_minute_data.csv`

---

## Data used

### Primary market data (Kalshi)

- API base: `https://api.elections.kalshi.com/trade-api/v2`
- Series: `KXBTCD` (hourly Bitcoin above/below contracts)
- Data pulled:
  - Market metadata
  - Settled markets
  - Minute candlesticks (`period_interval=1`)
  - Top-of-book quotes (via candlestick bid/ask fields)

### External BTC reference data

- Source: Coinbase `BTC-USD` candles
- Endpoint family: `https://api.exchange.coinbase.com/products/BTC-USD/candles`
- Granularity: 1 minute

### Data volume in this run

From `data/processed/feasibility_results.json`:

- Settled markets considered: **35,687**
- Markets selected for modeling: **360** (one liquid strike per event hour)
- Kalshi minute rows: **51,445**
- Coinbase minute rows: **29,414**
- Model-ready rows after joins/filters: **50,666**

### Data availability summary

- Historical contract prices: **Available**
- Minute-level history: **Available**
- Trade volume: **Available**
- Live order book snapshot: **Available**
- Public historical full-depth order book archive: **Not reliably available**

---

## Methodology

### 1) Market structure checks

Validated:
- Contract type and payout framing
- Settlement source (CF Benchmarks/BRTI)
- Tick size (`0.01` dollars per contract tick)
- Series fee type (`quadratic`, multiplier `1`)
- Position-limit language from contract terms

### 2) Dataset construction

- Pulled recent settled `KXBTCD` markets.
- Selected one high-volume contract per event hour (to avoid mixing many illiquid strikes).
- Pulled minute candlesticks for each selected contract.
- Derived minute mid-price, spread, and next-minute drift target.
- Joined with BTC minute returns and volatility features.

### 3) Features and hypotheses

Tested only simple hypotheses:

- **H1:** BTC short-term momentum predicts Kalshi probability drift.
- **H2:** High-volatility regimes increase directional predictability.
- **H3:** Kalshi reacts to BTC with a measurable lag.

Baseline features included:
- 1m BTC return
- 5m BTC momentum
- 15m BTC momentum
- 15m BTC realized volatility
- 1m market return

### 4) Modeling and validation

- Baseline models:
  - Logistic regression
  - Sign-based momentum
  - Volatility-regime momentum filter
- Validation:
  - Walk-forward time splits
  - Out-of-sample evaluation only for reported trading metrics
  - No look-ahead in feature/target construction

### 5) Transaction-cost modeling

PnL includes:
- Entry/exit through top-of-book bid/ask
- Slippage assumptions (1-2 ticks per side)
- Round-trip fee assumptions (scenario grid)

Break-even win-rate analysis computes required hit rate under each cost scenario.

---

## Problems encountered (and how they were handled)

1. **Kalshi candlestick API limit**
   - Problem: API rejects large windows (`max candlesticks: 5000`).
   - Fix: Implemented chunked minute-range requests and deduplication.

2. **Fee schedule access friction**
   - Problem: Direct access to Kalshi fee schedule PDF returned anti-bot checkpoint (HTTP 429).
   - Fix: Used API fee type/multiplier fields and model-level maker/taker fields; treated numerical fee schedule as partially constrained in this environment and used explicit scenario costs.

3. **Documentation drift**
   - Problem: Older filing text and current live market rule text are not always identical.
   - Fix: Prioritized live market-specific `rules_primary` and current API metadata for trading interpretation.

4. **Underlying price proxy mismatch**
   - Problem: Settlement references BRTI while model features used Coinbase BTC candles.
   - Fix: Flagged as basis-risk limitation in conclusions.

5. **Microstructure realism gap**
   - Problem: No robust public historical queue-depth archive for exact maker fill simulation.
   - Fix: Kept baseline conservative and cost-heavy; explicitly flagged limitation.

---

## Results

### Hypothesis diagnostics

- **H1 (momentum -> probability drift):** positive correlation (`~0.2468`, statistically strong)
- **H2 (volatility clustering improves predictability):** very small difference between high- and low-vol regimes
- **H3 (reaction lag):** strongest relation at lag `0` minute (little exploitable delayed reaction)

### Cost sensitivity (break-even)

In tested short-horizon scenarios, break-even win rates were all above 100%:

- 1c fee, 1 tick/side slippage: **125.21%**
- 2c fee, 1 tick/side slippage: **139.57%**
- 2c fee, 2 ticks/side slippage: **168.28%**
- 4c fee, 2 ticks/side slippage: **196.99%**

Interpretation: at this horizon and execution style, costs are too high relative to expected move size.

### Out-of-sample baseline performance (cost-adjusted)

- **Logistic:** 17,150 trades, win rate 13.70%, total return -600.80, Sharpe -32.71
- **Momentum:** 18,828 trades, win rate 10.63%, total return -846.52, Sharpe -36.79
- **Vol-regime:** 8,194 trades, win rate 12.90%, total return -352.05, Sharpe -27.75

All tested baselines were strongly negative after costs.

### Final decision

**NO-GO for the tested setup.**

This is not "no statistical relationship exists"; it is "relationship is not economically tradable after realistic frictions."

---

## Main bottlenecks

From strongest to weaker:

1. **Cost wall**: spread + slippage + fees exceed expected short-horizon edge.
2. **Execution realism gap**: limited historical queue-depth/fill-probability data.
3. **Low lag opportunity**: reaction appears mostly contemporaneous.
4. **Proxy risk**: settlement source differs from external BTC proxy feed.

---

## Suggested direction changes

If research continues, prioritize:

1. **Longer holding periods** (e.g., 5m/15m/30m exits)
2. **Relative-value structures** across strikes/events rather than pure direction
3. **Maker-first studies** only with real fill logs or authenticated historical order/fill data
4. **Time-to-close / settlement-window features**
5. **Strict market-selection filters** for liquidity/spread quality

---

## Reproduce

```bash
python3 analysis/kalshi_btc_feasibility.py
```

Generated files:

- `reports/feasibility_report.md`
- `data/processed/feasibility_results.json`
- `data/processed/market_minute_data.csv` (local intermediate output)

---

## Repository map

- `analysis/kalshi_btc_feasibility.py` - end-to-end study pipeline
- `reports/feasibility_report.md` - structured human-readable report
- `data/processed/feasibility_results.json` - numeric outputs and assumptions

---

## Scope and caveats

- This is a feasibility study, not a production strategy.
- It intentionally uses simple baselines to avoid overfitting and false confidence.
- Conclusions are conditional on current data access and execution assumptions.
