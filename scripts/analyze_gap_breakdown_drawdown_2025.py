from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


BASELINE_DIR = Path(
    r"D:\Trading\journal\baselines\manual-regime-baseline@1.0.0"
    r"\2024-01-01_2026-08-13"
)
AUDIT_PATH = Path(
    r"D:\Trading\journal\research\manual_regime_v3_full_equity_2026_08_13"
    r"\trade_audit.csv"
)
SPX_FEATURE_PATH = Path(
    r"D:\Trading\journal\research\spx_sideways_regime_definitions_2026_08_03"
    r"\spx_daily_sideways_definitions.csv"
)
RAW_DIR = Path(r"D:\Trading\Rohdaten")
OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\gap_breakdown_drawdown_2025_03_14_10_06"
)
RULE = "bearish_gap_breakdown_continuation_short"
PEAK_EXIT = pd.Timestamp("2025-03-14 23:59:59")
TROUGH_EXIT = pd.Timestamp("2025-10-06 23:59:59")


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    pnl = frame["pnl"].astype(float)
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    return {
        "trades": len(frame),
        "wins": int(pnl.gt(0).sum()),
        "losses": int(pnl.lt(0).sum()),
        "win_rate_pct": 100.0 * pnl.gt(0).mean() if len(frame) else np.nan,
        "net_pnl": pnl.sum(),
        "avg_pnl": pnl.mean() if len(frame) else np.nan,
        "profit_factor": gains / losses if losses else np.inf,
        "median_mfe_r": frame["mfe_r"].median() if "mfe_r" in frame else np.nan,
        "reached_0p5r_pct": 100.0 * frame["reached_0p5r"].mean()
        if "reached_0p5r" in frame and len(frame)
        else np.nan,
        "reached_1r_pct": 100.0 * frame["reached_1r"].mean()
        if "reached_1r" in frame and len(frame)
        else np.nan,
    }


def load_trades() -> pd.DataFrame:
    audit = pd.read_csv(AUDIT_PATH, parse_dates=["entry_date", "exit_date"])
    trades = audit[
        (audit["variant"] == "V3 current")
        & audit["executed"].astype(bool)
        & audit["rule"].eq(RULE)
    ].copy()
    source = pd.read_csv(
        BASELINE_DIR / "trades.csv", parse_dates=["entry_date", "exit_date"]
    )[["signal_key", "entry", "stop", "target", "side", "regime_at_entry"]]
    return trades.merge(source, on="signal_key", how="left", validate="one_to_one")


def load_contexts(keys: set[str]) -> pd.DataFrame:
    fields = [
        "atr14",
        "big_picture_regime",
        "big_picture_state",
        "daily_structure_phase",
        "market_phase",
        "volume_price_phase",
        "bearish_countermove_subregime",
        "mature_break",
        "true_breakdown_escalation",
        "bars_since_support_break",
        "distance_from_recent_high_atr",
        "profile_price_location",
        "profile_shape",
        "profile_balance_score",
        "profile_value_width_atr",
        "range_lower_subtype",
        "range_lower_risk_score",
        "range_lower_decision_vs_poc_atr",
        "range_lower_decision_vs_value_low_atr",
        "range_lower_open_vs_value_low_atr",
        "range_lower_value_pos",
        "breakdown_retest_from_below",
        "slow_drawdown_gap_filter",
        "post_break_midrange",
    ]
    rows: list[dict[str, object]] = []
    with (BASELINE_DIR / "decision_records.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            key = str(record.get("signal_key", ""))
            if key not in keys:
                continue
            context = record.get("context") or {}
            rows.append({"signal_key": key, **{field: context.get(field) for field in fields}})
    return pd.DataFrame(rows)


def daily_bars(symbol: str) -> pd.DataFrame:
    bars = pd.read_csv(
        RAW_DIR / f"Rohdaten_{symbol}.csv",
        usecols=["date", "open", "high", "low", "close", "volume"],
        parse_dates=["date"],
    ).sort_values("date")
    bars["day"] = bars["date"].dt.normalize()
    return (
        bars.groupby("day", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .sort_values("day")
        .reset_index(drop=True)
    )


def add_path_stats(trades: pd.DataFrame) -> pd.DataFrame:
    cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    for row in trades.itertuples():
        symbol = str(row.symbol)
        if symbol not in cache:
            cache[symbol] = pd.read_csv(
                RAW_DIR / f"Rohdaten_{symbol}.csv",
                usecols=["date", "high", "low", "close"],
                parse_dates=["date"],
            ).sort_values("date")
        bars = cache[symbol]
        window = bars[bars["date"].between(row.entry_date, row.exit_date)].copy()
        risk = float(row.stop) - float(row.entry)
        favorable = float(row.entry) - window["low"]
        adverse = window["high"] - float(row.entry)
        entry_day = pd.Timestamp(row.entry_date).normalize()
        first_day = window[window["date"].dt.normalize().eq(entry_day)]
        first_day_close = float(first_day.iloc[-1]["close"]) if len(first_day) else np.nan
        rows.append(
            {
                "signal_key": row.signal_key,
                "risk_points": risk,
                "target_r": (float(row.entry) - float(row.target)) / risk,
                "mfe_r": favorable.max() / risk,
                "mae_r": adverse.max() / risk,
                "reached_0p5r": bool(favorable.ge(0.5 * risk).any()),
                "reached_1r": bool(favorable.ge(risk).any()),
                "first_day_mfe_r": first_day.empty
                and np.nan
                or float((float(row.entry) - first_day["low"].min()) / risk),
                "first_day_close_r": (float(row.entry) - first_day_close) / risk,
                "holding_sessions": int(window["date"].dt.normalize().nunique()),
            }
        )
    return trades.merge(pd.DataFrame(rows), on="signal_key", validate="one_to_one")


def load_market_features(symbols: list[str]) -> pd.DataFrame:
    spx = pd.read_csv(SPX_FEATURE_PATH)
    spx["day"] = pd.to_datetime(spx["day"], utc=True).dt.tz_convert(
        "America/New_York"
    ).dt.tz_localize(None).dt.normalize()
    spx = spx.sort_values("day")
    spx["ema50"] = spx["close"].ewm(span=50, adjust=False).mean()
    spx["ema200"] = spx["close"].ewm(span=200, adjust=False).mean()
    spx["return_5_pct"] = 100.0 * spx["close"].pct_change(5)
    spx["return_20_pct"] = 100.0 * spx["close"].pct_change(20)
    spx["distance_ema20_atr"] = (spx["close"] - spx["ema20"]) / spx["atr14"]
    spx["ema20_slope_5_atr"] = (spx["ema20"] - spx["ema20"].shift(5)) / spx["atr14"]

    spy = daily_bars("SPY")
    spy["spy_volume_ratio20"] = spy["volume"] / spy["volume"].shift().rolling(20).mean()
    vix = daily_bars("VIX")[["day", "close"]].rename(columns={"close": "vix_close"})

    close_frames: list[pd.Series] = []
    ema20_frames: list[pd.Series] = []
    ema50_frames: list[pd.Series] = []
    for symbol in symbols:
        daily = daily_bars(symbol).set_index("day")["close"].astype(float)
        close_frames.append(daily.rename(symbol))
        ema20_frames.append(daily.ewm(span=20, adjust=False).mean().rename(symbol))
        ema50_frames.append(daily.ewm(span=50, adjust=False).mean().rename(symbol))
    closes = pd.concat(close_frames, axis=1)
    ema20 = pd.concat(ema20_frames, axis=1)
    ema50 = pd.concat(ema50_frames, axis=1)
    breadth = pd.DataFrame(
        {
            "breadth_above_ema20_pct": 100.0 * (closes > ema20).mean(axis=1),
            "breadth_above_ema50_pct": 100.0 * (closes > ema50).mean(axis=1),
        }
    ).reset_index(names="day")

    market = (
        spx.merge(spy[["day", "spy_volume_ratio20"]], on="day", how="left")
        .merge(vix, on="day", how="left")
        .merge(breadth, on="day", how="left")
    )
    columns = [
        "day",
        "close",
        "atr14",
        "adx14",
        "efficiency_20",
        "net_atr_20",
        "ema_crosses_20",
        "range_overlap_10x10",
        "sideways_score",
        "structure_score",
        "structure_phase",
        "ema20",
        "ema50",
        "ema200",
        "return_5_pct",
        "return_20_pct",
        "distance_ema20_atr",
        "ema20_slope_5_atr",
        "spy_volume_ratio20",
        "vix_close",
        "breadth_above_ema20_pct",
        "breadth_above_ema50_pct",
    ]
    market = market[columns].copy()
    market[columns[1:]] = market[columns[1:]].shift(1)
    market = market.rename(
        columns={column: f"spx_{column}" for column in columns if column != "day"}
    )
    return market


def add_asset_relative_features(trades: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in sorted(trades["symbol"].astype(str).unique()):
        daily = daily_bars(symbol)
        previous_close = daily["close"].shift()
        true_range = pd.concat(
            [
                daily["high"] - daily["low"],
                (daily["high"] - previous_close).abs(),
                (daily["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        daily["asset_atr14"] = true_range.ewm(
            alpha=1 / 14, adjust=False, min_periods=14
        ).mean()
        daily["asset_ema20"] = daily["close"].ewm(span=20, adjust=False).mean()
        daily["asset_ema50"] = daily["close"].ewm(span=50, adjust=False).mean()
        daily["asset_return_5_pct"] = 100.0 * daily["close"].pct_change(5)
        daily["asset_return_20_pct"] = 100.0 * daily["close"].pct_change(20)
        daily["asset_distance_ema20_atr"] = (
            daily["close"] - daily["asset_ema20"]
        ) / daily["asset_atr14"]
        columns = [
            "asset_return_5_pct",
            "asset_return_20_pct",
            "asset_distance_ema20_atr",
        ]
        daily[columns] = daily[columns].shift(1)
        daily["symbol"] = symbol
        frames.append(daily[["day", "symbol", *columns]])
    features = pd.concat(frames, ignore_index=True)
    result = trades.merge(
        features,
        left_on=["entry_day", "symbol"],
        right_on=["day", "symbol"],
        how="left",
        validate="many_to_one",
    ).drop(columns="day")
    result["relative_strength_5_pct"] = (
        result["asset_return_5_pct"] - result["spx_return_5_pct"]
    )
    result["relative_strength_20_pct"] = (
        result["asset_return_20_pct"] - result["spx_return_20_pct"]
    )
    return result


def classify_segments(trades: pd.DataFrame) -> pd.DataFrame:
    result = trades.sort_values(["exit_date", "signal_key"]).reset_index(drop=True)
    drawdown = result["exit_date"].gt(PEAK_EXIT) & result["exit_date"].le(TROUGH_EXIT)
    indices = result.index[drawdown]
    first, last = int(indices.min()), int(indices.max())
    count = len(indices)
    result["comparison_segment"] = "other"
    result.loc[max(0, first - count) : first - 1, "comparison_segment"] = "prior_37"
    result.loc[first:last, "comparison_segment"] = "drawdown_37"
    result.loc[last + 1 : min(len(result) - 1, last + count), "comparison_segment"] = "post_37"

    day = result["entry_date"].dt.normalize()
    result["market_subphase"] = np.select(
        [
            day.lt(pd.Timestamp("2025-03-15")),
            day.between(pd.Timestamp("2025-03-15"), pd.Timestamp("2025-04-07")),
            day.between(pd.Timestamp("2025-04-08"), pd.Timestamp("2025-04-29")),
            day.between(pd.Timestamp("2025-04-30"), pd.Timestamp("2025-06-25")),
            day.between(pd.Timestamp("2025-06-26"), pd.Timestamp("2025-09-17")),
            day.between(pd.Timestamp("2025-09-18"), pd.Timestamp("2025-10-06")),
        ],
        [
            "preexisting_positions",
            "selloff_to_apr07",
            "v_repair_and_range",
            "bullish_recovery",
            "rising_range",
            "renewed_bullish_trend",
        ],
        default="outside",
    )
    result["spx_direction_bucket"] = pd.cut(
        result["spx_net_atr_20"],
        [-np.inf, -2.0, 0.0, 2.0, np.inf],
        labels=["strong_bear", "mild_bear", "mild_bull", "strong_bull"],
    )
    return result


def grouped_metrics(frame: pd.DataFrame, group_column: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for value, group in frame.groupby(group_column, dropna=False, observed=True):
        rows.append({group_column: value, **metrics(group)})
    return pd.DataFrame(rows).sort_values("net_pnl")


def categorical_compare(
    trades: pd.DataFrame, columns: list[str]
) -> pd.DataFrame:
    selected = trades[trades["comparison_segment"].isin(["prior_37", "drawdown_37", "post_37"])]
    rows: list[dict[str, object]] = []
    for column in columns:
        for (segment, value), group in selected.groupby(
            ["comparison_segment", column], dropna=False, observed=True
        ):
            rows.append(
                {
                    "feature": column,
                    "segment": segment,
                    "value": value,
                    **metrics(group),
                }
            )
    return pd.DataFrame(rows)


def numeric_compare(trades: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    selected = trades[trades["comparison_segment"].isin(["prior_37", "drawdown_37", "post_37"])]
    rows: list[dict[str, object]] = []
    for segment, group in selected.groupby("comparison_segment"):
        for column in columns:
            values = pd.to_numeric(group[column], errors="coerce")
            rows.append(
                {
                    "segment": segment,
                    "feature": column,
                    "count": values.notna().sum(),
                    "mean": values.mean(),
                    "median": values.median(),
                    "q25": values.quantile(0.25),
                    "q75": values.quantile(0.75),
                }
            )
    return pd.DataFrame(rows)


def protective_stop_variants(trades: pd.DataFrame) -> pd.DataFrame:
    cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    for trigger_r, lock_r in ((0.50, 0.0), (0.75, 0.0), (1.00, 0.0), (1.00, 0.10)):
        simulated: list[dict[str, object]] = []
        for trade in trades.itertuples():
            symbol = str(trade.symbol)
            if symbol not in cache:
                cache[symbol] = pd.read_csv(
                    RAW_DIR / f"Rohdaten_{symbol}.csv",
                    usecols=["date", "high", "low"],
                    parse_dates=["date"],
                ).sort_values("date")
            bars = cache[symbol]
            path = bars[
                bars["date"].gt(trade.entry_date)
                & bars["date"].le(trade.exit_date)
            ]
            risk = float(trade.stop) - float(trade.entry)
            trigger = float(trade.entry) - trigger_r * risk
            protected_stop = float(trade.entry) - lock_r * risk
            armed = False
            pnl = float(trade.pnl)
            exit_time = pd.Timestamp(trade.exit_date)
            for bar in path.itertuples():
                if armed and float(bar.high) >= protected_stop:
                    pnl = (float(trade.entry) - protected_stop) * int(trade.quantity)
                    exit_time = pd.Timestamp(bar.date)
                    break
                if pd.Timestamp(bar.date) >= pd.Timestamp(trade.exit_date):
                    break
                if float(bar.low) <= trigger:
                    armed = True
            simulated.append(
                {
                    "signal_key": trade.signal_key,
                    "pnl": pnl,
                    "exit_time": exit_time,
                    "entry_year": pd.Timestamp(trade.entry_date).year,
                    "comparison_segment": trade.comparison_segment,
                }
            )
        frame = pd.DataFrame(simulated).sort_values(["exit_time", "signal_key"])
        pnl = frame["pnl"]
        gains = pnl[pnl > 0].sum()
        losses = -pnl[pnl < 0].sum()
        equity = pnl.cumsum()
        row: dict[str, object] = {
            "variant": f"trigger_{trigger_r:.2f}R_lock_{lock_r:.2f}R",
            "net_pnl": pnl.sum(),
            "profit_factor": gains / losses if losses else np.inf,
            "max_drawdown": (equity - equity.cummax()).min(),
            "drawdown_37_pnl": frame.loc[
                frame["comparison_segment"].eq("drawdown_37"), "pnl"
            ].sum(),
        }
        for year, group in frame.groupby("entry_year"):
            row[f"net_{year}"] = group["pnl"].sum()
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    trades = load_trades()
    trades = trades.merge(
        load_contexts(set(trades["signal_key"].astype(str))),
        on="signal_key",
        how="left",
        validate="one_to_one",
    )
    trades = add_path_stats(trades)
    all_audit = pd.read_csv(AUDIT_PATH)
    basket_symbols = sorted(
        set(
            all_audit.loc[
                (all_audit["variant"] == "V3 current")
                & all_audit["executed"].astype(bool),
                "symbol",
            ].astype(str)
        )
        - {"SPX", "SPX_FUT"}
    )
    market = load_market_features(basket_symbols)
    trades["entry_day"] = trades["entry_date"].dt.normalize()
    trades = trades.merge(
        market, left_on="entry_day", right_on="day", how="left", validate="many_to_one"
    ).drop(columns="day")
    trades = add_asset_relative_features(trades)
    trades = classify_segments(trades)

    selected = trades[
        trades["comparison_segment"].isin(["prior_37", "drawdown_37", "post_37"])
    ]
    segment_summary = grouped_metrics(selected, "comparison_segment")
    loss_phase = trades[trades["comparison_segment"].eq("drawdown_37")].copy()
    subphase_summary = grouped_metrics(loss_phase, "market_subphase")
    direction_summary_all = grouped_metrics(trades, "spx_direction_bucket")
    direction_summary_loss = grouped_metrics(loss_phase, "spx_direction_bucket")

    categorical_columns = [
        "spx_direction_bucket",
        "spx_structure_phase",
        "big_picture_regime",
        "big_picture_state",
        "daily_structure_phase",
        "volume_price_phase",
        "profile_price_location",
        "range_lower_subtype",
        "true_breakdown_escalation",
    ]
    numeric_columns = [
        "spx_adx14",
        "spx_efficiency_20",
        "spx_net_atr_20",
        "spx_sideways_score",
        "spx_structure_score",
        "spx_return_5_pct",
        "spx_return_20_pct",
        "spx_distance_ema20_atr",
        "spx_ema20_slope_5_atr",
        "spx_vix_close",
        "spx_breadth_above_ema20_pct",
        "spx_breadth_above_ema50_pct",
        "asset_return_5_pct",
        "asset_return_20_pct",
        "asset_distance_ema20_atr",
        "relative_strength_5_pct",
        "relative_strength_20_pct",
        "distance_from_recent_high_atr",
        "range_lower_decision_vs_poc_atr",
        "range_lower_decision_vs_value_low_atr",
        "profile_balance_score",
        "profile_value_width_atr",
        "mfe_r",
        "first_day_mfe_r",
        "first_day_close_r",
        "holding_sessions",
    ]
    category_compare = categorical_compare(trades, categorical_columns)
    number_compare = numeric_compare(trades, numeric_columns)
    stop_variants = protective_stop_variants(trades)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUTPUT_DIR / "all_gap_trades_enriched.csv", index=False)
    loss_phase.to_csv(OUTPUT_DIR / "drawdown_37_trades.csv", index=False)
    segment_summary.to_csv(OUTPUT_DIR / "comparison_segment_summary.csv", index=False)
    subphase_summary.to_csv(OUTPUT_DIR / "drawdown_market_subphase_summary.csv", index=False)
    direction_summary_all.to_csv(OUTPUT_DIR / "spx_direction_summary_all_261.csv", index=False)
    direction_summary_loss.to_csv(OUTPUT_DIR / "spx_direction_summary_drawdown_37.csv", index=False)
    category_compare.to_csv(OUTPUT_DIR / "categorical_context_compare.csv", index=False)
    number_compare.to_csv(OUTPUT_DIR / "numeric_context_compare.csv", index=False)
    stop_variants.to_csv(OUTPUT_DIR / "protective_stop_variants.csv", index=False)

    print("Equal-count segment comparison")
    print(segment_summary.round(3).to_string(index=False))
    print("\nDrawdown by SPX subphase")
    print(subphase_summary.round(3).to_string(index=False))
    print("\nSPX direction: all 261")
    print(direction_summary_all.round(3).to_string(index=False))
    print("\nSPX direction: drawdown 37")
    print(direction_summary_loss.round(3).to_string(index=False))
    print("\nNumeric medians")
    print(
        number_compare.pivot(index="feature", columns="segment", values="median")
        .round(3)
        .to_string()
    )
    print("\nLoss-phase profile locations")
    print(
        grouped_metrics(loss_phase, "profile_price_location")
        .round(3)
        .to_string(index=False)
    )
    print("\nProtective stop variants")
    print(stop_variants.round(3).to_string(index=False))
    print(f"\nwrote={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
