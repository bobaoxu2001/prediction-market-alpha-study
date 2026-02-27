# prediction-market-alpha-study

Feasibility study for trading Kalshi hourly Bitcoin (`KXBTCD`) contracts.

## Reproduce

```bash
python3 analysis/kalshi_btc_feasibility.py
```

Default outputs:

- `reports/feasibility_report.md`
- `data/processed/feasibility_results.json`
- `data/processed/market_minute_data.csv`

## Notes

- The study is intentionally skeptical and focuses on structural viability, not alpha optimization.
- Public Kalshi data is pulled from `https://api.elections.kalshi.com/trade-api/v2`.
- BTC minute proxy data is pulled from Coinbase (`BTC-USD` candles).
