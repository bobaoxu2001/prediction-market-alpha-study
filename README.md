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
