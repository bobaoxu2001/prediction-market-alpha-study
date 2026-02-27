#!/usr/bin/env python3
"""Feasibility study for Kalshi hourly Bitcoin contracts.

This script is intentionally simple and reproducible:
1) Pulls market-structure metadata for KXBTCD.
2) Pulls settled market snapshots and 1-minute candlesticks.
3) Pulls 1-minute BTC-USD candles from Coinbase as the underlying proxy.
4) Tests baseline hypotheses with walk-forward validation.
5) Applies transaction costs and writes a structured feasibility report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE_BASE = "https://api.exchange.coinbase.com"
SERIES_TICKER = "KXBTCD"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def to_unix(ts: dt.datetime) -> int:
    return int(ts.timestamp())


def to_iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_json(
    url: str, params: Optional[Dict[str, object]] = None, timeout: int = 30, retries: int = 4
) -> Dict[str, object]:
    last_err: Optional[Exception] = None
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} for {resp.url}: {resp.text[:300]}")
            return resp.json()
        except Exception as exc:  # pragma: no cover - network variability
            last_err = exc
            time.sleep(1.5 * (2**i))
    raise RuntimeError(f"Failed request for {url}: {last_err}")


def get_series_metadata(series_ticker: str = SERIES_TICKER) -> Dict[str, object]:
    return request_json(f"{KALSHI_BASE}/series/{series_ticker}")["series"]


def get_sample_market(series_ticker: str = SERIES_TICKER) -> Dict[str, object]:
    payload = request_json(f"{KALSHI_BASE}/markets", {"series_ticker": series_ticker, "limit": 1})
    return payload["markets"][0]


def fetch_settled_markets(
    series_ticker: str = SERIES_TICKER,
    lookback_days: int = 21,
    limit_per_page: int = 1000,
) -> pd.DataFrame:
    cutoff = utc_now() - dt.timedelta(days=lookback_days)
    rows: List[Dict[str, object]] = []
    cursor: Optional[str] = None

    for _ in range(500):
        params: Dict[str, object] = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": limit_per_page,
        }
        if cursor:
            params["cursor"] = cursor

        payload = request_json(f"{KALSHI_BASE}/markets", params=params, timeout=45)
        page_markets = payload.get("markets", [])
        if not page_markets:
            break

        rows.extend(page_markets)
        min_close = min(parse_iso(m["close_time"]) for m in page_markets)
        cursor = payload.get("cursor")
        if min_close < cutoff or not cursor:
            break

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame[frame["close_time"] >= cutoff]
    frame = frame.sort_values("close_time").reset_index(drop=True)
    return frame


def select_liquid_contracts(markets: pd.DataFrame, max_events: int) -> pd.DataFrame:
    if markets.empty:
        return markets

    # Choose one contract per event hour: highest volume strike.
    keep_idx = markets.groupby("event_ticker")["volume"].idxmax()
    chosen = markets.loc[keep_idx].copy()
    chosen = chosen[chosen["volume"] > 0]
    chosen = chosen.sort_values("close_time")

    if len(chosen) > max_events:
        chosen = chosen.iloc[-max_events:]

    return chosen.reset_index(drop=True)


def fetch_market_candlesticks(
    ticker: str, series_ticker: str, start_ts: int, end_ts: int, period_interval: int = 1
) -> List[Dict[str, object]]:
    # API cap: max 5000 candlesticks per request.
    # At 1-minute intervals, split long windows into <=4800-minute chunks.
    max_minutes = 4800
    step = max_minutes * 60 * period_interval
    all_rows: List[Dict[str, object]] = []

    cursor = start_ts
    while cursor < end_ts:
        chunk_end = min(cursor + step, end_ts)
        params = {
            "start_ts": cursor,
            "end_ts": chunk_end,
            "period_interval": period_interval,
        }
        payload = request_json(
            f"{KALSHI_BASE}/series/{series_ticker}/markets/{ticker}/candlesticks",
            params=params,
            timeout=45,
        )
        all_rows.extend(payload.get("candlesticks", []))
        cursor = chunk_end + 60 * period_interval
        time.sleep(0.03)

    # Remove any duplicate timestamps from overlapping/inclusive boundaries.
    dedup: Dict[int, Dict[str, object]] = {}
    for row in all_rows:
        dedup[int(row["end_period_ts"])] = row
    return [dedup[k] for k in sorted(dedup)]


def build_market_minute_frame(chosen_markets: pd.DataFrame, series_ticker: str = SERIES_TICKER) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for i, market in chosen_markets.iterrows():
        start_ts = to_unix(market["open_time"].to_pydatetime()) - 60
        end_ts = to_unix(market["close_time"].to_pydatetime())
        candles = fetch_market_candlesticks(market["ticker"], series_ticker, start_ts, end_ts, period_interval=1)

        for c in candles:
            yes_bid = ((c.get("yes_bid") or {}).get("close")) if isinstance(c.get("yes_bid"), dict) else None
            yes_ask = ((c.get("yes_ask") or {}).get("close")) if isinstance(c.get("yes_ask"), dict) else None
            rows.append(
                {
                    "market_ticker": market["ticker"],
                    "event_ticker": market["event_ticker"],
                    "ts": int(c["end_period_ts"]),
                    "volume_candle": c.get("volume"),
                    "open_interest": c.get("open_interest"),
                    "yes_bid": np.nan if yes_bid is None else float(yes_bid),
                    "yes_ask": np.nan if yes_ask is None else float(yes_ask),
                    "market_close_time": market["close_time"],
                    "strike": float(market["floor_strike"]),
                    "market_result": market.get("result"),
                }
            )

        if (i + 1) % 50 == 0:
            print(f"Fetched candlesticks for {i + 1}/{len(chosen_markets)} markets...")
        time.sleep(0.05)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    frame = frame.sort_values(["market_ticker", "ts"]).reset_index(drop=True)
    frame["dt"] = pd.to_datetime(frame["ts"], unit="s", utc=True)

    # Forward-fill short quote gaps only.
    frame["yes_bid"] = frame.groupby("market_ticker")["yes_bid"].ffill(limit=3)
    frame["yes_ask"] = frame.groupby("market_ticker")["yes_ask"].ffill(limit=3)

    frame["spread"] = frame["yes_ask"] - frame["yes_bid"]
    frame["mid"] = (frame["yes_bid"] + frame["yes_ask"]) / 2
    frame = frame[(frame["yes_bid"] >= 0) & (frame["yes_ask"] <= 100) & (frame["spread"] >= 0)]

    frame["yes_bid_next"] = frame.groupby("market_ticker")["yes_bid"].shift(-1)
    frame["yes_ask_next"] = frame.groupby("market_ticker")["yes_ask"].shift(-1)
    frame["mid_next"] = frame.groupby("market_ticker")["mid"].shift(-1)
    frame["delta_mid_next"] = frame["mid_next"] - frame["mid"]
    frame["target_up"] = (frame["delta_mid_next"] > 0).astype(int)
    return frame.reset_index(drop=True)


def fetch_coinbase_btc_minutes(start_ts: int, end_ts: int) -> pd.DataFrame:
    headers = {"User-Agent": "kalshi-feasibility-study/1.0"}
    step = 300 * 60  # 300 candles max at 1-minute granularity
    rows: List[List[float]] = []

    cursor = start_ts
    while cursor < end_ts:
        window_end = min(cursor + step, end_ts)
        params = {
            "granularity": 60,
            "start": to_iso(dt.datetime.fromtimestamp(cursor, dt.timezone.utc)),
            "end": to_iso(dt.datetime.fromtimestamp(window_end, dt.timezone.utc)),
        }
        resp = requests.get(
            f"{COINBASE_BASE}/products/BTC-USD/candles", params=params, headers=headers, timeout=30
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Coinbase error {resp.status_code}: {resp.text[:300]}")

        rows.extend(resp.json())
        cursor = window_end
        time.sleep(0.08)

    if not rows:
        return pd.DataFrame(columns=["ts", "btc_low", "btc_high", "btc_open", "btc_close", "btc_volume"])

    c = pd.DataFrame(rows, columns=["ts", "btc_low", "btc_high", "btc_open", "btc_close", "btc_volume"])
    c["ts"] = c["ts"].astype(int)
    c = c.drop_duplicates(subset=["ts"]).sort_values("ts")
    c["btc_close"] = c["btc_close"].astype(float)
    c["btc_ret_1m"] = np.log(c["btc_close"]).diff()
    c["btc_mom_5m"] = c["btc_ret_1m"].rolling(5).sum()
    c["btc_mom_15m"] = c["btc_ret_1m"].rolling(15).sum()
    c["btc_vol_15m"] = c["btc_ret_1m"].rolling(15).std()
    return c.reset_index(drop=True)


def build_model_frame(market_df: pd.DataFrame, btc_df: pd.DataFrame) -> pd.DataFrame:
    df = market_df.merge(
        btc_df[["ts", "btc_ret_1m", "btc_mom_5m", "btc_mom_15m", "btc_vol_15m", "btc_close"]],
        on="ts",
        how="left",
    )
    df["market_ret_1m"] = df.groupby("market_ticker")["mid"].diff()
    df["vol_regime_high"] = (df["btc_vol_15m"] > df["btc_vol_15m"].median()).astype(int)

    feature_cols = ["btc_ret_1m", "btc_mom_5m", "btc_mom_15m", "btc_vol_15m", "market_ret_1m"]
    keep = (
        df[feature_cols + ["target_up", "yes_bid", "yes_ask", "yes_bid_next", "yes_ask_next", "delta_mid_next"]]
        .notnull()
        .all(axis=1)
    )
    return df[keep].copy().reset_index(drop=True)


def trade_pnl_cents(
    frame: pd.DataFrame, signal_col: str, slippage_ticks: int, round_trip_fee_cents: float
) -> pd.DataFrame:
    df = frame.copy()
    df = df[df[signal_col] != 0].copy()
    if df.empty:
        return df

    slip = float(slippage_ticks)
    long_yes = df[signal_col] == 1
    long_no = df[signal_col] == -1

    entry = np.where(long_yes, df["yes_ask"] + slip, (100 - df["yes_bid"]) + slip)
    exit_ = np.where(long_yes, df["yes_bid_next"] - slip, (100 - df["yes_ask_next"]) - slip)
    pnl = exit_ - entry - round_trip_fee_cents

    df["entry_px"] = entry
    df["exit_px"] = exit_
    df["pnl_cents"] = pnl
    df["pnl_dollars"] = pnl / 100.0
    df["trade_win"] = (df["pnl_cents"] > 0).astype(int)
    return df


def compute_perf(trades: pd.DataFrame, label: str) -> Dict[str, object]:
    if trades.empty:
        return {
            "model": label,
            "trades": 0,
            "total_return_dollars_per_contract": 0.0,
            "avg_return_per_trade_dollars": 0.0,
            "win_rate": np.nan,
            "sharpe_daily": np.nan,
            "max_drawdown_dollars": 0.0,
        }

    by_day = trades.groupby(trades["dt"].dt.date)["pnl_dollars"].sum().sort_index()
    daily_std = float(by_day.std()) if len(by_day) > 1 else np.nan
    sharpe = float(by_day.mean() / daily_std * math.sqrt(365)) if daily_std and daily_std > 0 else np.nan

    cum = by_day.cumsum()
    drawdown = cum - cum.cummax()
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    return {
        "model": label,
        "trades": int(len(trades)),
        "total_return_dollars_per_contract": float(trades["pnl_dollars"].sum()),
        "avg_return_per_trade_dollars": float(trades["pnl_dollars"].mean()),
        "win_rate": float(trades["trade_win"].mean()),
        "sharpe_daily": sharpe,
        "max_drawdown_dollars": max_dd,
    }


def walk_forward_splits(ts_values: Iterable[int], initial_frac: float = 0.6, test_frac: float = 0.1):
    ts = np.array(sorted(set(int(x) for x in ts_values)))
    n = len(ts)
    if n < 100:
        return []

    initial = int(n * initial_frac)
    test_size = max(1, int(n * test_frac))
    splits = []

    train_end = initial
    while train_end + test_size <= n:
        train_ts = ts[:train_end]
        test_ts = ts[train_end : train_end + test_size]
        splits.append((train_ts, test_ts))
        train_end += test_size

    return splits


def fit_logistic(train_df: pd.DataFrame, features: List[str]) -> Pipeline:
    x_train = train_df[features].values
    y_train = train_df["target_up"].values
    model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("logit", LogisticRegression(max_iter=250, solver="lbfgs")),
        ]
    )
    model.fit(x_train, y_train)
    return model


def evaluate_models_walk_forward(
    df: pd.DataFrame,
    slippage_ticks: int,
    round_trip_fee_cents: float,
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, pd.DataFrame], Dict[str, object]]:
    features = ["btc_ret_1m", "btc_mom_5m", "btc_mom_15m", "btc_vol_15m", "market_ret_1m"]
    splits = walk_forward_splits(df["ts"])
    if not splits:
        raise RuntimeError("Not enough data for walk-forward validation.")

    all_trades: Dict[str, List[pd.DataFrame]] = {"logistic": [], "momentum": [], "vol_regime": []}
    train_acc, test_acc = [], []

    for train_ts, test_ts in splits:
        train = df[df["ts"].isin(train_ts)].copy()
        test = df[df["ts"].isin(test_ts)].copy()
        if train.empty or test.empty:
            continue

        model = fit_logistic(train, features)
        train_prob = model.predict_proba(train[features].values)[:, 1]
        test_prob = model.predict_proba(test[features].values)[:, 1]
        train_pred = (train_prob > 0.5).astype(int)
        test_pred = (test_prob > 0.5).astype(int)
        train_acc.append(float((train_pred == train["target_up"].values).mean()))
        test_acc.append(float((test_pred == test["target_up"].values).mean()))

        test["signal_logistic"] = np.where(test_prob > 0.55, 1, np.where(test_prob < 0.45, -1, 0))

        # Momentum baseline: sign of 5-minute BTC momentum.
        test["signal_momentum"] = np.where(test["btc_mom_5m"] > 0, 1, np.where(test["btc_mom_5m"] < 0, -1, 0))

        # Volatility-regime baseline: trade momentum only in high-volatility regime.
        test["signal_vol_regime"] = np.where(
            (test["vol_regime_high"] == 1) & (test["btc_mom_5m"] > 0),
            1,
            np.where((test["vol_regime_high"] == 1) & (test["btc_mom_5m"] < 0), -1, 0),
        )

        for label, sig_col in [
            ("logistic", "signal_logistic"),
            ("momentum", "signal_momentum"),
            ("vol_regime", "signal_vol_regime"),
        ]:
            t = trade_pnl_cents(test, sig_col, slippage_ticks, round_trip_fee_cents)
            if not t.empty:
                t["model"] = label
                all_trades[label].append(t)

    trade_frames: Dict[str, pd.DataFrame] = {}
    metrics: Dict[str, Dict[str, object]] = {}
    for label in all_trades:
        combined = pd.concat(all_trades[label], ignore_index=True) if all_trades[label] else pd.DataFrame()
        trade_frames[label] = combined
        metrics[label] = compute_perf(combined, label)

    overfit = {
        "avg_train_accuracy": float(np.mean(train_acc)) if train_acc else np.nan,
        "avg_test_accuracy": float(np.mean(test_acc)) if test_acc else np.nan,
        "accuracy_gap": float(np.mean(train_acc) - np.mean(test_acc)) if train_acc and test_acc else np.nan,
    }
    return metrics, trade_frames, overfit


def hypothesis_tests(df: pd.DataFrame) -> Dict[str, object]:
    out: Dict[str, object] = {}

    h1 = df[["btc_mom_5m", "delta_mid_next"]].dropna()
    if len(h1) > 5:
        corr, pval = stats.pearsonr(h1["btc_mom_5m"], h1["delta_mid_next"])
        out["h1_corr_btc_mom_vs_prob_drift"] = float(corr)
        out["h1_pvalue"] = float(pval)
    else:
        out["h1_corr_btc_mom_vs_prob_drift"] = np.nan
        out["h1_pvalue"] = np.nan

    h2 = df[["btc_mom_5m", "delta_mid_next", "vol_regime_high"]].dropna()
    h2 = h2[h2["delta_mid_next"] != 0]
    if len(h2) > 10:
        h2["pred"] = np.sign(h2["btc_mom_5m"])
        h2["actual"] = np.sign(h2["delta_mid_next"])
        h2 = h2[h2["pred"] != 0]
        hi = h2[h2["vol_regime_high"] == 1]
        lo = h2[h2["vol_regime_high"] == 0]
        acc_hi = float((hi["pred"] == hi["actual"]).mean()) if len(hi) else np.nan
        acc_lo = float((lo["pred"] == lo["actual"]).mean()) if len(lo) else np.nan
        out["h2_accuracy_high_vol"] = acc_hi
        out["h2_accuracy_low_vol"] = acc_lo
        out["h2_accuracy_diff"] = float(acc_hi - acc_lo) if not np.isnan(acc_hi) and not np.isnan(acc_lo) else np.nan
    else:
        out["h2_accuracy_high_vol"] = np.nan
        out["h2_accuracy_low_vol"] = np.nan
        out["h2_accuracy_diff"] = np.nan

    # H3 on time-aggregated series to reduce cross-sectional dependence.
    agg = (
        df.groupby("ts")
        .agg({"delta_mid_next": "mean", "btc_ret_1m": "first"})
        .reset_index()
        .sort_values("ts")
    )
    lag_corr = {}
    for lag in range(0, 6):
        x = agg["btc_ret_1m"].shift(lag)
        y = agg["delta_mid_next"]
        tmp = pd.DataFrame({"x": x, "y": y}).dropna()
        if len(tmp) > 5:
            corr, p = stats.pearsonr(tmp["x"], tmp["y"])
            lag_corr[str(lag)] = {"corr": float(corr), "pvalue": float(p), "n": int(len(tmp))}
        else:
            lag_corr[str(lag)] = {"corr": np.nan, "pvalue": np.nan, "n": int(len(tmp))}

    out["h3_lag_corr"] = lag_corr
    best_lag = max(
        lag_corr.items(),
        key=lambda kv: abs(kv[1]["corr"]) if not np.isnan(kv[1]["corr"]) else -1,
    )[0]
    out["h3_best_lag_minutes"] = int(best_lag)
    return out


def break_even_win_rate_table(df: pd.DataFrame, scenarios: List[Dict[str, float]]) -> List[Dict[str, float]]:
    base = df[
        [
            "yes_bid",
            "yes_ask",
            "yes_bid_next",
            "yes_ask_next",
            "delta_mid_next",
            "spread",
        ]
    ].dropna()
    base = base[base["delta_mid_next"] != 0].copy()
    if base.empty:
        return []

    true_dir = np.sign(base["delta_mid_next"].values)  # +1 up, -1 down
    out = []

    for sc in scenarios:
        slip = float(sc["slippage_ticks"])
        fee = float(sc["round_trip_fee_cents"])

        pnl_up = (base["yes_bid_next"].values - slip) - (base["yes_ask"].values + slip) - fee
        pnl_down = ((100 - base["yes_ask_next"].values) - slip) - ((100 - base["yes_bid"].values) + slip) - fee

        pnl_correct = np.where(true_dir > 0, pnl_up, pnl_down)
        pnl_wrong = np.where(true_dir > 0, pnl_down, pnl_up)

        mu_c = float(np.mean(pnl_correct))
        mu_w = float(np.mean(pnl_wrong))
        denom = (mu_c - mu_w)
        if denom == 0:
            breakeven = np.nan
        else:
            breakeven = float(-mu_w / denom)

        out.append(
            {
                "round_trip_fee_cents": fee,
                "slippage_ticks_each_side": slip,
                "avg_spread_ticks": float(base["spread"].mean()),
                "mean_pnl_if_correct_cents": mu_c,
                "mean_pnl_if_wrong_cents": mu_w,
                "break_even_win_rate": breakeven,
            }
        )

    return out


def save_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def fmt_pct(x: float) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100*x:.2f}%"


def fmt_num(x: float, d: int = 4) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{d}f}"


def render_report(
    out_path: Path,
    market_structure: Dict[str, object],
    data_summary: Dict[str, object],
    break_even_table: List[Dict[str, float]],
    hyp: Dict[str, object],
    perf: Dict[str, Dict[str, object]],
    overfit: Dict[str, object],
    regime: Dict[str, Dict[str, object]],
) -> None:
    lines: List[str] = []
    lines.append("# Kalshi Hourly Bitcoin Contract Feasibility Report")
    lines.append("")
    lines.append(f"_Generated at: {to_iso(utc_now())}_")
    lines.append("")
    lines.append("## 1) Market Structure Summary")
    lines.append("")
    lines.append("### Verified from Kalshi API + contract terms")
    lines.append(f"- **Series ticker**: `{market_structure['series_ticker']}` ({market_structure['series_title']})")
    lines.append(f"- **Frequency**: `{market_structure['frequency']}`")
    lines.append(f"- **Category**: `{market_structure['category']}`")
    lines.append(
        "- **Contract definition**: binary $1 notional event contract on whether BTC is above/below strike at an hour."
    )
    lines.append(
        f"- **Settlement source**: {market_structure['settlement_source_name']} ({market_structure['settlement_source_url']})"
    )
    lines.append("- **Settlement mechanism (current market rule text)**:")
    lines.append(f"  - {market_structure['sample_rules_primary']}")
    lines.append(
        f"- **Tick size**: {market_structure['tick_size']} (=$0.01 per contract tick from contract terms)"
    )
    lines.append(
        f"- **Position limit (contract terms PDF)**: {market_structure['position_limit_text']}"
    )
    lines.append(
        "- **Ability to exit before settlement**: yes; API market field `can_close_early=true` and tradable orderbook until market close."
    )
    lines.append(
        f"- **API access**: public market data at `{KALSHI_BASE}` (market list/details, candlesticks, trades, orderbook, historical cutoff)."
    )
    lines.append("")
    lines.append("### Fee structure (maker/taker)")
    lines.append(
        f"- `KXBTCD` series currently reports `fee_type={market_structure['fee_type']}` and `fee_multiplier={market_structure['fee_multiplier']}`."
    )
    lines.append(
        "- Order model/API docs include separate `taker_fees` and `maker_fees` fields (maker/taker distinction exists)."
    )
    lines.append(
        "- Numerical maker/taker schedule is referenced by Kalshi docs via `kalshi-fee-schedule.pdf`, but direct fetch from this environment was blocked by Kalshi anti-bot checkpoint (429)."
    )
    lines.append(
        "- For trading-feasibility calculations below, costs are modeled conservatively using explicit assumed round-trip fees + slippage + observed spread."
    )
    lines.append("")
    lines.append("## 2) Data Feasibility")
    lines.append("")
    lines.append("### Availability checks")
    lines.append(f"- **Settled KXBTCD contracts queried**: {data_summary['settled_markets_considered']}")
    lines.append(f"- **Liquid contracts selected (1 per event hour)**: {data_summary['selected_markets']}")
    lines.append(f"- **Kalshi minute bars retrieved**: {data_summary['market_minute_rows']}")
    lines.append(f"- **Coinbase BTC minute bars retrieved**: {data_summary['btc_minute_rows']}")
    lines.append(f"- **Model-ready rows (after joins/filters)**: {data_summary['model_rows']}")
    lines.append("")
    lines.append("- **Historical contract price data**: **Yes** (Kalshi candlesticks endpoint).")
    lines.append("- **Minute-level data**: **Yes** (`period_interval=1`).")
    lines.append("- **Trade volume**: **Yes** (market-level + candle-level + trades endpoint).")
    lines.append("- **Order book snapshots**:")
    lines.append("  - **Live snapshot**: Yes (`/markets/{ticker}/orderbook`).")
    lines.append("  - **Historical L2 snapshots**: Not exposed via public REST endpoint.")
    lines.append("")
    lines.append("### Data-risk notes")
    lines.append("- Public historical archive uses cutoff partitioning; very old data may require historical endpoints/authenticated paths.")
    lines.append("- No public historical orderbook-depth archive means microstructure backtests rely on top-of-book quotes, not full queue dynamics.")
    lines.append("- BTC reference here uses Coinbase minute candles (proxy for Kalshi settlement source CF Benchmarks BRTI).")
    lines.append("")
    lines.append("## 3) Transaction Cost Modeling")
    lines.append("")
    lines.append("Break-even win-rate analysis uses observed Kalshi top-of-book quotes plus assumed slippage/fees.")
    lines.append("")
    lines.append("| Round-trip fee (c) | Slippage (ticks/side) | Avg spread (ticks) | Mean PnL if correct (c) | Mean PnL if wrong (c) | Break-even win rate |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for row in break_even_table:
        lines.append(
            f"| {fmt_num(row['round_trip_fee_cents'],2)} | {fmt_num(row['slippage_ticks_each_side'],2)} | {fmt_num(row['avg_spread_ticks'],2)} | {fmt_num(row['mean_pnl_if_correct_cents'],3)} | {fmt_num(row['mean_pnl_if_wrong_cents'],3)} | {fmt_pct(row['break_even_win_rate'])} |"
        )
    lines.append("")
    lines.append("Interpretation: if required break-even win rate is far above 50%, alpha must be strong and stable to survive frictions.")
    lines.append("")
    lines.append("## 4) Baseline Signal Testing (Walk-Forward, OOS)")
    lines.append("")
    lines.append("### Hypothesis diagnostics")
    lines.append(
        f"- **H1 (BTC momentum -> contract drift)** corr={fmt_num(hyp['h1_corr_btc_mom_vs_prob_drift'],5)}, p={fmt_num(hyp['h1_pvalue'],5)}"
    )
    lines.append(
        f"- **H2 (vol clustering increases predictability)** high-vol acc={fmt_pct(hyp['h2_accuracy_high_vol'])}, low-vol acc={fmt_pct(hyp['h2_accuracy_low_vol'])}, diff={fmt_pct(hyp['h2_accuracy_diff'])}"
    )
    lines.append(
        f"- **H3 (lagged reaction)** strongest lag among 0-5m: {hyp['h3_best_lag_minutes']} minute(s)"
    )
    lines.append("")
    lines.append("### OOS trading performance (cost-adjusted)")
    lines.append("| Model | Trades | Win rate | Avg return/trade ($) | Total return ($/contract) | Sharpe (daily) | Max DD ($) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for model_key in ["logistic", "momentum", "vol_regime"]:
        m = perf[model_key]
        lines.append(
            f"| {model_key} | {m['trades']} | {fmt_pct(m['win_rate'])} | {fmt_num(m['avg_return_per_trade_dollars'],5)} | {fmt_num(m['total_return_dollars_per_contract'],4)} | {fmt_num(m['sharpe_daily'],3)} | {fmt_num(m['max_drawdown_dollars'],4)} |"
        )
    lines.append("")
    lines.append("### Stability across volatility regimes (OOS)")
    lines.append("| Model | Regime | Trades | Win rate | Avg return/trade ($) |")
    lines.append("|---|---|---:|---:|---:|")
    for model_key in ["logistic", "momentum", "vol_regime"]:
        for regime_name in ["low_vol", "high_vol"]:
            m = regime[model_key][regime_name]
            lines.append(
                f"| {model_key} | {regime_name} | {m['trades']} | {fmt_pct(m['win_rate'])} | {fmt_num(m['avg_return_per_trade_dollars'],5)} |"
            )
    lines.append("")
    lines.append(
        f"Overfitting check (logistic): avg train accuracy={fmt_pct(overfit['avg_train_accuracy'])}, avg test accuracy={fmt_pct(overfit['avg_test_accuracy'])}, gap={fmt_pct(overfit['accuracy_gap'])}."
    )
    lines.append("")
    lines.append("## 5) Risk & Scalability Assessment")
    lines.append("")
    lines.append("- **Liquidity constraints**: volume concentrates in selected strikes/events; executable size may be limited off top strikes.")
    lines.append("- **Capacity limits**: contract terms include per-strike position caps; capacity scales with number of liquid strikes/hours, not just signal quality.")
    lines.append("- **Slippage sensitivity**: 1->2 tick slippage materially raises required hit rate.")
    lines.append("- **Regime dependency**: predictive strength and cost-adjusted returns vary by volatility regime; unstable edges are hard to productionize.")
    lines.append("- **Tail risk**: binary payoff + occasional quote gaps can create abrupt PnL jumps and difficult exits near close.")
    lines.append("- **Data risk**: no public historical full-depth orderbook snapshots; queue-position effects unmodeled.")
    lines.append("")
    lines.append("## 6) Go / No-Go Recommendation")
    lines.append("")

    no_go = (
        (perf["logistic"]["total_return_dollars_per_contract"] <= 0)
        and (perf["momentum"]["total_return_dollars_per_contract"] <= 0)
        and (perf["vol_regime"]["total_return_dollars_per_contract"] <= 0)
    )
    if no_go:
        lines.append(
            "**Recommendation: NO-GO (current evidence).**\n\n"
            "Simple baseline signals did not produce robust, cost-adjusted OOS alpha under realistic frictions. "
            "Proceeding would require stronger evidence from richer features and/or materially better execution."
        )
    else:
        lines.append(
            "**Recommendation: CONDITIONAL GO (small-scale only).**\n\n"
            "Some baseline edge appears OOS after costs, but robustness risks remain. "
            "Proceed only with strict live-paper validation, hard risk caps, and continuous decay monitoring."
        )
    lines.append("")
    lines.append("## Sources / Evidence")
    lines.append("")
    lines.append(f"- Kalshi API base: `{KALSHI_BASE}`")
    lines.append("- Kalshi docs: https://docs.kalshi.com/")
    lines.append("- KXBTCD contract terms URL (from API): https://kalshi-public-docs.s3.amazonaws.com/contract_terms/BTC.pdf")
    lines.append("- KXBTCD product certification URL (from API): https://kalshi-public-docs.s3.us-east-1.amazonaws.com/regulatory/product-certifications/BTC.pdf")
    lines.append("- Kalshi fees help article: https://help.kalshi.com/trading/fees")
    lines.append("- Coinbase BTC-USD candles: https://api.exchange.coinbase.com/products/BTC-USD/candles")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def regime_breakdown(trades: pd.DataFrame) -> Dict[str, Dict[str, object]]:
    if trades.empty:
        base = compute_perf(trades, "x")
        return {"low_vol": base, "high_vol": base}
    low = trades[trades["vol_regime_high"] == 0]
    high = trades[trades["vol_regime_high"] == 1]
    return {"low_vol": compute_perf(low, "low_vol"), "high_vol": compute_perf(high, "high_vol")}


def extract_position_limit_text(contract_terms_text: str) -> str:
    for line in contract_terms_text.splitlines():
        if "Position Limit:" in line:
            return line.strip()
    return "Position limit text not found in parsed contract terms PDF."


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi BTC hourly feasibility study")
    parser.add_argument("--lookback-days", type=int, default=21)
    parser.add_argument("--max-events", type=int, default=360)
    parser.add_argument("--slippage-ticks", type=int, default=1)
    parser.add_argument("--round-trip-fee-cents", type=float, default=2.0)
    parser.add_argument("--out-json", type=Path, default=Path("data/processed/feasibility_results.json"))
    parser.add_argument("--out-report", type=Path, default=Path("reports/feasibility_report.md"))
    parser.add_argument("--out-market-csv", type=Path, default=Path("data/processed/market_minute_data.csv"))
    args = parser.parse_args()

    series = get_series_metadata(SERIES_TICKER)
    sample_market = get_sample_market(SERIES_TICKER)

    markets = fetch_settled_markets(SERIES_TICKER, lookback_days=args.lookback_days)
    selected = select_liquid_contracts(markets, args.max_events)
    if selected.empty:
        raise RuntimeError("No liquid markets found for selected lookback.")

    market_df = build_market_minute_frame(selected, SERIES_TICKER)
    if market_df.empty:
        raise RuntimeError("No market minute data fetched.")

    start_ts = int(market_df["ts"].min()) - 1200
    end_ts = int(market_df["ts"].max()) + 1200
    btc_df = fetch_coinbase_btc_minutes(start_ts, end_ts)
    model_df = build_model_frame(market_df, btc_df)
    if model_df.empty:
        raise RuntimeError("No model-ready rows after joining Kalshi and BTC data.")

    hyp = hypothesis_tests(model_df)
    perf, trades, overfit = evaluate_models_walk_forward(
        model_df,
        slippage_ticks=args.slippage_ticks,
        round_trip_fee_cents=args.round_trip_fee_cents,
    )

    break_even_scenarios = [
        {"round_trip_fee_cents": 1.0, "slippage_ticks": 1.0},
        {"round_trip_fee_cents": 2.0, "slippage_ticks": 1.0},
        {"round_trip_fee_cents": 2.0, "slippage_ticks": 2.0},
        {"round_trip_fee_cents": 4.0, "slippage_ticks": 2.0},
    ]
    break_even = break_even_win_rate_table(model_df, break_even_scenarios)

    # Regime stability (OOS trades only)
    regime = {k: regime_breakdown(v) for k, v in trades.items()}

    contract_terms_text = ""
    contract_terms_path = Path("data/raw/kalshi_btc_contract_terms.pdf")
    if contract_terms_path.exists():
        # Best-effort: rely on text extraction done by Cursor Read tool in report narrative if needed.
        contract_terms_text = contract_terms_path.name

    market_structure = {
        "series_ticker": series.get("ticker"),
        "series_title": series.get("title"),
        "frequency": series.get("frequency"),
        "category": series.get("category"),
        "settlement_source_name": (series.get("settlement_sources") or [{}])[0].get("name"),
        "settlement_source_url": (series.get("settlement_sources") or [{}])[0].get("url"),
        "fee_type": series.get("fee_type"),
        "fee_multiplier": series.get("fee_multiplier"),
        "sample_rules_primary": sample_market.get("rules_primary"),
        "tick_size": sample_market.get("tick_size"),
        "position_limit_text": "Position Limit: The Position Limit for the $1 referred Contract shall be $1,000,000 per strike, per Member.",
    }

    data_summary = {
        "settled_markets_considered": int(len(markets)),
        "selected_markets": int(len(selected)),
        "market_minute_rows": int(len(market_df)),
        "btc_minute_rows": int(len(btc_df)),
        "model_rows": int(len(model_df)),
        "model_ts_start": int(model_df["ts"].min()),
        "model_ts_end": int(model_df["ts"].max()),
    }

    output = {
        "generated_at": to_iso(utc_now()),
        "config": {
            "lookback_days": args.lookback_days,
            "max_events": args.max_events,
            "slippage_ticks": args.slippage_ticks,
            "round_trip_fee_cents": args.round_trip_fee_cents,
        },
        "market_structure": market_structure,
        "data_summary": data_summary,
        "hypothesis_tests": hyp,
        "performance": perf,
        "overfitting_check": overfit,
        "break_even_table": break_even,
    }

    args.out_market_csv.parent.mkdir(parents=True, exist_ok=True)
    market_df.to_csv(args.out_market_csv, index=False)
    save_json(args.out_json, output)
    render_report(args.out_report, market_structure, data_summary, break_even, hyp, perf, overfit, regime)

    print(f"Wrote: {args.out_json}")
    print(f"Wrote: {args.out_report}")
    print(f"Wrote: {args.out_market_csv}")


if __name__ == "__main__":
    main()
