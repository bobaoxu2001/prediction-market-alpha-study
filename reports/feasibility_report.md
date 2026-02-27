# Kalshi Hourly Bitcoin Contract Feasibility Report

_Generated at: 2026-02-27T08:09:59Z_

## 1) Market Structure Summary

### Verified from Kalshi API + contract terms
- **Series ticker**: `KXBTCD` (Bitcoin price Above/below)
- **Frequency**: `hourly`
- **Category**: `Crypto`
- **Contract definition**: binary $1 notional event contract on whether BTC is above/below strike at an hour.
- **Settlement source**: CF Benchmarks (https://www.cfbenchmarks.com/data/indices/BRTI?ref=blog.cfbenchmarks.com)
- **Settlement mechanism (current market rule text)**:
  - If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index (BRTI) before 12 AM EST is above 77749.99 at 12 AM EST on Feb 28, 2026, then the market resolves to Yes.
- **Tick size**: 1 (=$0.01 per contract tick from contract terms)
- **Position limit (contract terms PDF)**: Position Limit: The Position Limit for the $1 referred Contract shall be $1,000,000 per strike, per Member.
- **Ability to exit before settlement**: yes; API market field `can_close_early=true` and tradable orderbook until market close.
- **API access**: public market data at `https://api.elections.kalshi.com/trade-api/v2` (market list/details, candlesticks, trades, orderbook, historical cutoff).
- **Documentation drift risk**: older 2024 product-certification text differs from current market rule text (e.g., averaging window and historical limit language). For trading, rely on live market-specific `rules_primary` + current contract terms from the API.

### Fee structure (maker/taker)
- `KXBTCD` series currently reports `fee_type=quadratic` and `fee_multiplier=1`.
- Order model/API docs include separate `taker_fees` and `maker_fees` fields (maker/taker distinction exists).
- Numerical maker/taker schedule is referenced by Kalshi docs via `kalshi-fee-schedule.pdf`, but direct fetch from this environment was blocked by Kalshi anti-bot checkpoint (429).
- For trading-feasibility calculations below, costs are modeled conservatively using explicit assumed round-trip fees + slippage + observed spread.

## 2) Data Feasibility

### Availability checks
- **Settled KXBTCD contracts queried**: 35687
- **Liquid contracts selected (1 per event hour)**: 360
- **Kalshi minute bars retrieved**: 51445
- **Coinbase BTC minute bars retrieved**: 29414
- **Model-ready rows (after joins/filters)**: 50666

- **Historical contract price data**: **Yes** (Kalshi candlesticks endpoint).
- **Minute-level data**: **Yes** (`period_interval=1`).
- **Trade volume**: **Yes** (market-level + candle-level + trades endpoint).
- **Order book snapshots**:
  - **Live snapshot**: Yes (`/markets/{ticker}/orderbook`).
  - **Historical L2 snapshots**: Not exposed via public REST endpoint.

### Data-risk notes
- Public historical archive uses cutoff partitioning; very old data may require historical endpoints/authenticated paths.
- No public historical orderbook-depth archive means microstructure backtests rely on top-of-book quotes, not full queue dynamics.
- BTC reference here uses Coinbase minute candles (proxy for Kalshi settlement source CF Benchmarks BRTI).

## 3) Transaction Cost Modeling

Break-even win-rate analysis uses observed Kalshi top-of-book quotes plus assumed slippage/fees.

| Round-trip fee (c) | Slippage (ticks/side) | Avg spread (ticks) | Mean PnL if correct (c) | Mean PnL if wrong (c) | Break-even win rate |
|---:|---:|---:|---:|---:|---:|
| 1.00 | 1.00 | 2.24 | -1.756 | -8.722 | 125.21% |
| 2.00 | 1.00 | 2.24 | -2.756 | -9.722 | 139.57% |
| 2.00 | 2.00 | 2.24 | -4.756 | -11.722 | 168.28% |
| 4.00 | 2.00 | 2.24 | -6.756 | -13.722 | 196.99% |

Interpretation: all scenarios imply break-even win rates above 100%, i.e., **even a perfect directional classifier would still lose** at a 1-minute holding horizon under these execution assumptions. This is a structural cost hurdle, not just a model-quality issue.

## 4) Baseline Signal Testing (Walk-Forward, OOS)

### Hypothesis diagnostics
- **H1 (BTC momentum -> contract drift)** corr=0.24685, p=0.00000
- **H2 (vol clustering increases predictability)** high-vol acc=64.44%, low-vol acc=64.66%, diff=-0.23%
- **H3 (lagged reaction)** strongest lag among 0-5m: 0 minute(s)

### OOS trading performance (cost-adjusted)
| Model | Trades | Win rate | Avg return/trade ($) | Total return ($/contract) | Sharpe (daily) | Max DD ($) |
|---|---:|---:|---:|---:|---:|---:|
| logistic | 17150 | 13.70% | -0.03503 | -600.8000 | -32.709 | -595.6500 |
| momentum | 18828 | 10.63% | -0.04496 | -846.5200 | -36.787 | -839.9400 |
| vol_regime | 8194 | 12.90% | -0.04296 | -352.0500 | -27.750 | -297.0000 |

### Stability across volatility regimes (OOS)
| Model | Regime | Trades | Win rate | Avg return/trade ($) |
|---|---|---:|---:|---:|
| logistic | low_vol | 9521 | 10.20% | -0.04239 |
| logistic | high_vol | 7629 | 18.08% | -0.02585 |
| momentum | low_vol | 10634 | 8.88% | -0.04650 |
| momentum | high_vol | 8194 | 12.90% | -0.04296 |
| vol_regime | low_vol | 0 | n/a | 0.00000 |
| vol_regime | high_vol | 8194 | 12.90% | -0.04296 |

Overfitting check (logistic): avg train accuracy=75.62%, avg test accuracy=76.39%, gap=-0.77%.

## 5) Risk & Scalability Assessment

- **Liquidity constraints**: volume concentrates in selected strikes/events; executable size may be limited off top strikes.
- **Capacity limits**: contract terms include per-strike position caps; capacity scales with number of liquid strikes/hours, not just signal quality.
- **Slippage sensitivity**: 1->2 tick slippage materially raises required hit rate.
- **Regime dependency**: predictive strength and cost-adjusted returns vary by volatility regime; unstable edges are hard to productionize.
- **Tail risk**: binary payoff + occasional quote gaps can create abrupt PnL jumps and difficult exits near close.
- **Data risk**: no public historical full-depth orderbook snapshots; queue-position effects unmodeled.

## 6) Go / No-Go Recommendation

**Recommendation: NO-GO (current evidence).**

Simple baseline signals did not produce robust, cost-adjusted OOS alpha under realistic frictions. Proceeding would require stronger evidence from richer features and/or materially better execution.

## Sources / Evidence

- Kalshi API base: `https://api.elections.kalshi.com/trade-api/v2`
- Kalshi docs: https://docs.kalshi.com/
- KXBTCD contract terms URL (from API): https://kalshi-public-docs.s3.amazonaws.com/contract_terms/BTC.pdf
- KXBTCD product certification URL (from API): https://kalshi-public-docs.s3.us-east-1.amazonaws.com/regulatory/product-certifications/BTC.pdf
- Kalshi fees help article: https://help.kalshi.com/trading/fees
- Coinbase BTC-USD candles: https://api.exchange.coinbase.com/products/BTC-USD/candles
