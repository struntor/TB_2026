from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ENGINE_DIR = Path(
    r"D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03"
)
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import compare_lhf_cluster_dedup_top50_equity as cluster_tools
from manual_regime_position_sizing import (
    build_position_sizing_context,
    risk_multiplier,
)


BASELINE_DIR = Path(
    r"D:\Trading\journal\baselines\manual-regime-baseline@1.0.0"
    r"\2024-01-01_2026-08-13"
)
V2_SIZING_PATH = Path(
    r"D:\Trading\journal\research\regime_position_sizing_variants_2026_08_03"
    r"\trades_by_sizing_variant.csv"
)
FRESH_STOP_PATH = Path(
    r"D:\Trading\journal\research\failed_countermove_fresh_confirmation"
    r"\trade_audit.csv"
)
DEFAULT_OUTPUT_DIR = Path(
    r"D:\Trading\journal\research\manual_regime_v3_full_equity_2026_08_13"
)
TARGET_RULE = "bearish_failed_countermove_short_baseline"
V2_VARIANT = "graded_75_50_plus_volume"
FRESH_VARIANT = "fresh_stop_structure_only"
HISTORICAL_SIZING_END = pd.Timestamp("2026-07-31 23:59:59")
START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-08-13")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--html-output", type=Path)
    return parser.parse_args()


def write_html_fragment(payload: dict[str, object], path: Path) -> None:
    template = r'''<div id="manual-regime-v3-full-equity">
  <style>
    #manual-regime-v3-full-equity { position: relative; color: var(--foreground); width: 100%; max-width: 100%; min-width: 0; box-sizing: border-box; overflow-x: hidden; }
    #manual-regime-v3-full-equity h2 { margin: 0 0 8px 0; font-weight: 500; white-space: normal; overflow-wrap: anywhere; }
    #manual-regime-v3-full-equity .legend { margin-bottom: 8px; gap: 14px; align-items: center; }
    #manual-regime-v3-full-equity .series-toggle { background: transparent; border: 0; color: var(--foreground); padding: 4px 0; display: inline-flex; align-items: center; gap: 7px; }
    #manual-regime-v3-full-equity .series-toggle[aria-pressed="false"] { color: var(--muted-foreground); }
    #manual-regime-v3-full-equity .swatch { width: 24px; height: 3px; display: inline-block; background: var(--viz-series-1); }
    #manual-regime-v3-full-equity .swatch.parent { background: var(--viz-series-2); height: 2px; }
    #manual-regime-v3-full-equity .chart-wrap { width: 100%; max-width: 100%; min-width: 0; min-height: 360px; }
    #manual-regime-v3-full-equity svg { display: block; width: 100%; height: auto; }
    #manual-regime-v3-full-equity .axis text,
    #manual-regime-v3-full-equity .axis-title,
    #manual-regime-v3-full-equity .endpoint-label,
    #manual-regime-v3-full-equity .annotation-label { fill: var(--foreground); font-size: 12px; }
    #manual-regime-v3-full-equity .axis path,
    #manual-regime-v3-full-equity .axis line,
    #manual-regime-v3-full-equity .chart-frame { stroke: var(--border); }
    #manual-regime-v3-full-equity .grid line { stroke: var(--border); stroke-opacity: 0.55; }
    #manual-regime-v3-full-equity .grid path { display: none; }
    #manual-regime-v3-full-equity .series-v3 { stroke: var(--viz-series-1); }
    #manual-regime-v3-full-equity .series-v2 { stroke: var(--viz-series-2); stroke-dasharray: 6 4; }
    #manual-regime-v3-full-equity .amzn-mark { fill: var(--viz-series-1); stroke: var(--background); }
    #manual-regime-v3-full-equity .hover-guide { stroke: var(--muted-foreground); stroke-width: 1px; }
    #manual-regime-v3-full-equity .hover-marker-v3 { fill: var(--viz-series-1); }
    #manual-regime-v3-full-equity .hover-marker-v2 { fill: var(--viz-series-2); }
    #manual-regime-v3-full-equity .tooltip { position: absolute; pointer-events: none; display: none; background: var(--popover); color: var(--popover-foreground); border: 1px solid var(--border); padding: 8px 10px; z-index: 2; }
    #manual-regime-v3-full-equity .caption { margin-top: 4px; color: var(--muted-foreground); }
    @media (max-width: 480px) {
      #manual-regime-v3-full-equity .legend { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
      #manual-regime-v3-full-equity .series-toggle { white-space: normal; }
      #manual-regime-v3-full-equity .caption { white-space: normal; overflow-wrap: anywhere; }
    }
  </style>
  <h2>Manual-Regime V3 · alle Assets</h2>
  <div class="viz-row legend" aria-label="Datenreihen">
    <button type="button" class="series-toggle" data-series="V3 current" aria-pressed="true"><span class="swatch"></span>V3 aktuell · 36.693 USD</button>
    <button type="button" class="series-toggle" data-series="V2 parent shadow" aria-pressed="true"><span class="swatch parent"></span>V2 Parent-Shadow · 36.289 USD</button>
  </div>
  <div class="chart-wrap"><svg role="img" aria-labelledby="mrv3-title mrv3-desc"><title id="mrv3-title">Kumulierte Equity aller Assets und Regeln</title><desc id="mrv3-desc">Vergleich der vollständigen Manual-Regime V3 Equity-Kurve mit dem V2 Parent-Shadow von Januar 2024 bis 13. August 2026.</desc></svg></div>
  <div class="caption text-small">1.082 Signale · alle Assets und Regeln · AMZN als EOD-Mark enthalten.</div>
  <div class="tooltip" role="tooltip"></div>
  <script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
  <script>
  (() => {
    const root = document.getElementById('manual-regime-v3-full-equity');
    const payload = __DATA__;
    const parseDate = d3.timeParse('%Y-%m-%d');
    const names = ['V3 current', 'V2 parent shadow'];
    const values = Object.fromEntries(names.map(name => [name, payload.series[name].map(d => ({date: parseDate(d.date), value: +d.value}))]));
    const visible = new Set(names);
    const svg = d3.select(root.querySelector('svg'));
    const wrap = root.querySelector('.chart-wrap');
    const tooltip = root.querySelector('.tooltip');
    const money = new Intl.NumberFormat('de-DE', {maximumFractionDigits: 0});
    const dateFmt = d3.timeFormat('%d.%m.%Y');

    function interpolate(series, date) {
      const bisect = d3.bisector(d => d.date).center;
      const i = bisect(series, date);
      const b = series[Math.max(0, Math.min(series.length - 1, i))];
      const a = series[Math.max(0, i - 1)];
      if (!a || a.date.getTime() === b.date.getTime()) return b.value;
      const t = (date - a.date) / (b.date - a.date);
      return a.value + t * (b.value - a.value);
    }

    function draw() {
      const width = Math.max(320, Math.floor(wrap.getBoundingClientRect().width));
      const height = width < 480 ? 360 : 460;
      const margin = {top: 18, right: width < 480 ? 24 : 118, bottom: 54, left: 72};
      const innerWidth = width - margin.left - margin.right;
      const innerHeight = height - margin.top - margin.bottom;
      svg.attr('viewBox', `0 0 ${width} ${height}`);
      svg.selectAll('*:not(title):not(desc)').remove();
      const shown = names.filter(name => visible.has(name));
      const all = shown.flatMap(name => values[name]);
      const xExtent = d3.extent(names.flatMap(name => values[name]), d => d.date);
      const yExtent = d3.extent(all.length ? all : names.flatMap(name => values[name]), d => d.value);
      const yPad = Math.max(500, (yExtent[1] - yExtent[0]) * 0.06);
      const x = d3.scaleTime().domain(xExtent).range([0, innerWidth]);
      const y = d3.scaleLinear().domain([Math.min(0, yExtent[0] - yPad), yExtent[1] + yPad]).nice().range([innerHeight, 0]);
      const g = svg.append('g').attr('transform', `translate(${margin.left},${margin.top})`);
      g.append('rect').attr('class', 'chart-frame').attr('data-chart-frame', '').attr('width', innerWidth).attr('height', innerHeight).attr('fill', 'none');
      g.append('g').attr('class', 'grid').call(d3.axisLeft(y).ticks(6).tickSize(-innerWidth).tickFormat(''));
      const tickInterval = width < 480 ? d3.timeYear.every(1) : d3.timeMonth.every(6);
      const xAxis = g.append('g').attr('class', 'axis').attr('transform', `translate(0,${innerHeight})`).call(d3.axisBottom(x).ticks(tickInterval).tickFormat(d3.timeFormat('%m/%Y')));
      const xTicks = xAxis.selectAll('.tick text');
      xTicks.attr('text-anchor', (d, i, nodes) => i === 0 ? 'start' : (i === nodes.length - 1 ? 'end' : 'middle'));
      g.append('g').attr('class', 'axis').call(d3.axisLeft(y).ticks(6).tickFormat(d => money.format(d)));
      svg.append('text').attr('class', 'axis-title').attr('data-axis', 'x').attr('text-anchor', 'middle').attr('x', margin.left + innerWidth / 2).attr('y', height - 8).text('Exit-Datum');
      svg.append('text').attr('class', 'axis-title').attr('data-axis', 'y').attr('text-anchor', 'middle').attr('transform', `translate(16,${margin.top + innerHeight / 2}) rotate(-90)`).text('Kumulierter P&L (USD)');
      const line = d3.line().x(d => x(d.date)).y(d => y(d.value));
      shown.forEach(name => {
        g.append('path').datum(values[name]).attr('class', name === 'V3 current' ? 'series-v3' : 'series-v2').attr('fill', 'none').attr('stroke-width', name === 'V3 current' ? 2.4 : 1.8).attr('d', line);
        const end = values[name][values[name].length - 1];
        if (width >= 480) g.append('text').attr('class', 'endpoint-label').attr('x', innerWidth + 7).attr('y', y(end.value) + (name === 'V3 current' ? -4 : 13)).text(name === 'V3 current' ? `V3 ${money.format(end.value)}` : `V2 ${money.format(end.value)}`);
      });
      if (visible.has('V3 current')) {
        const amzn = values['V3 current'][values['V3 current'].length - 1];
        g.append('circle').attr('class', 'amzn-mark').attr('cx', x(amzn.date)).attr('cy', y(amzn.value)).attr('r', 4);
        if (width >= 620) g.append('text').attr('class', 'annotation-label').attr('text-anchor', 'end').attr('x', x(amzn.date) - 8).attr('y', y(amzn.value) - 10).text('AMZN +151 USD (EOD-Mark)');
      }
      const guide = g.append('line').attr('class', 'hover-guide').attr('data-chart-hover-guide', '').attr('y1', 0).attr('y2', innerHeight).style('display', 'none');
      const markers = Object.fromEntries(names.map(name => [name, g.append('circle').attr('class', name === 'V3 current' ? 'hover-marker-v3' : 'hover-marker-v2').attr('r', 4).style('display', 'none')]));
      g.append('rect').attr('data-chart-hit', '').attr('data-chart-hover-overlay', 'cross-series').attr('width', innerWidth).attr('height', innerHeight).attr('fill', 'transparent')
        .on('pointermove', event => {
          const [px] = d3.pointer(event);
          const date = x.invert(Math.max(0, Math.min(innerWidth, px)));
          guide.attr('x1', px).attr('x2', px).style('display', null);
          const rows = [];
          shown.forEach(name => {
            const value = interpolate(values[name], date);
            markers[name].attr('cx', px).attr('cy', y(value)).style('display', null);
            rows.push(`${name}: ${money.format(value)} USD`);
          });
          names.filter(name => !visible.has(name)).forEach(name => markers[name].style('display', 'none'));
          tooltip.innerHTML = `<strong>${dateFmt(date)}</strong><br>${rows.join('<br>')}`;
          tooltip.style.display = 'block';
          const bounds = root.getBoundingClientRect();
          const left = Math.min(bounds.width - tooltip.offsetWidth - 4, margin.left + px + 12);
          tooltip.style.left = `${Math.max(4, left)}px`;
          tooltip.style.top = `${Math.max(4, margin.top + y(interpolate(values[shown[0]], date)) - 54)}px`;
        })
        .on('pointerleave', () => {
          guide.style('display', 'none');
          names.forEach(name => markers[name].style('display', 'none'));
          tooltip.style.display = 'none';
        });
    }
    root.querySelectorAll('.series-toggle').forEach(button => button.addEventListener('click', () => {
      const name = button.dataset.series;
      if (visible.has(name) && visible.size > 1) visible.delete(name); else visible.add(name);
      button.setAttribute('aria-pressed', String(visible.has(name)));
      draw();
    }));
    new ResizeObserver(draw).observe(wrap);
    draw();
  })();
  </script>
</div>
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        template.replace("__DATA__", json.dumps(payload, ensure_ascii=False)),
        encoding="utf-8",
    )


def load_cluster_selected_trades() -> pd.DataFrame:
    ledger = pd.read_csv(
        BASELINE_DIR / "model_trades_including_non_routable.csv",
        parse_dates=["entry_date", "exit_date"],
    )
    ledger["symbol"] = ledger["symbol"].astype(str).str.upper()
    ledger = ledger[~ledger["symbol"].isin({"SPX", "SPX_FUT"})].copy()
    ledger["_source_index"] = np.arange(len(ledger), dtype=int)
    _, audit = cluster_tools.choose_lhf_cluster_trades(ledger)
    removed = set(
        audit.loc[~audit["selected"].astype(bool), "signal_key"].astype(str)
    )

    trades = pd.read_csv(
        BASELINE_DIR / "trades.csv",
        parse_dates=["entry_date", "exit_date"],
    )
    trades["symbol"] = trades["symbol"].astype(str).str.upper()
    selected = trades[~trades["signal_key"].astype(str).isin(removed)].copy()
    if selected["signal_key"].duplicated().any():
        raise RuntimeError("Cluster-selected trades contain duplicate signal keys")
    return selected.sort_values(["entry_date", "symbol"]).reset_index(drop=True)


def attach_v2_risk_multiplier(trades: pd.DataFrame) -> pd.DataFrame:
    historical = pd.read_csv(V2_SIZING_PATH)
    historical = historical[historical["variant"] == V2_VARIANT].copy()
    historical = historical[["signal_key", "risk_multiplier"]].drop_duplicates(
        "signal_key"
    )
    result = trades.merge(
        historical,
        on="signal_key",
        how="left",
        validate="one_to_one",
    )
    old_missing = result[
        result["risk_multiplier"].isna()
        & result["entry_date"].le(HISTORICAL_SIZING_END)
    ]
    if not old_missing.empty:
        raise RuntimeError(
            f"Missing historical V2 sizing for {len(old_missing)} trades"
        )

    contexts: dict[pd.Timestamp, object] = {}
    for index, row in result[result["risk_multiplier"].isna()].iterrows():
        day = pd.Timestamp(row["entry_date"]).normalize()
        if day not in contexts:
            contexts[day] = build_position_sizing_context(day)
        multiplier, _ = risk_multiplier(
            symbol=str(row["symbol"]),
            rule=str(row["rule"]),
            side=str(row["side"]),
            context=contexts[day],
        )
        result.at[index, "risk_multiplier"] = multiplier
    if result["risk_multiplier"].isna().any():
        raise RuntimeError("V2 sizing merge left missing multipliers")
    return result


def exact_quantity(row: pd.Series, stop: float) -> int:
    risk = abs(float(row["entry"]) - float(stop))
    if not math.isfinite(risk) or risk <= 0:
        return 0
    budget = 100.0 * float(row.get("size", 1.0)) * float(row["risk_multiplier"])
    return max(0, math.floor(budget / risk))


def build_parent_v2(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for _, row in trades.iterrows():
        quantity = exact_quantity(row, float(row["stop"]))
        pnl_per_share = (
            float(row["pnl_dollars"]) / float(row["quantity"])
            if float(row["quantity"]) > 0
            else 0.0
        )
        rows.append(
            {
                "signal_key": row["signal_key"],
                "symbol": row["symbol"],
                "rule": row["rule"],
                "entry_date": row["entry_date"],
                "exit_date": row["exit_date"],
                "quantity": quantity,
                "pnl": pnl_per_share * quantity,
                "exit_reason": row["exit_reason"],
                "marked_open": False,
                "risk_multiplier": row["risk_multiplier"],
            }
        )
    return pd.DataFrame(rows)


def build_v3(trades: pd.DataFrame) -> pd.DataFrame:
    fresh = pd.read_csv(
        FRESH_STOP_PATH,
        parse_dates=["entry_date_new", "exit_date_new"],
    )
    fresh = fresh[fresh["variant"] == FRESH_VARIANT].copy()
    fresh = fresh[
        [
            "signal_key",
            "executed",
            "entry_new",
            "stop_new",
            "exit_date_new",
            "pnl_new",
            "quantity_new",
            "exit_reason_new",
            "marked_open",
        ]
    ].drop_duplicates("signal_key")
    merged = trades.merge(fresh, on="signal_key", how="left", validate="one_to_one")
    affected = merged["rule"].eq(TARGET_RULE)
    if merged.loc[affected, "executed"].isna().any():
        raise RuntimeError("Fresh-stop audit is missing affected signals")

    rows: list[dict[str, object]] = []
    for _, row in merged.iterrows():
        if str(row["rule"]) != TARGET_RULE:
            quantity = exact_quantity(row, float(row["stop"]))
            pnl_per_share = (
                float(row["pnl_dollars"]) / float(row["quantity"])
                if float(row["quantity"]) > 0
                else 0.0
            )
            pnl = pnl_per_share * quantity
            exit_date = row["exit_date"]
            exit_reason = row["exit_reason"]
            marked_open = False
            executed = quantity > 0
        elif not bool(row["executed"]):
            quantity = 0
            pnl = 0.0
            exit_date = pd.NaT
            exit_reason = "fresh_structure_stop_crv_below_1"
            marked_open = False
            executed = False
        else:
            risk = float(row["stop_new"]) - float(row["entry_new"])
            budget = (
                100.0
                * float(row.get("size", 1.0))
                * float(row["risk_multiplier"])
            )
            quantity = max(0, math.floor(budget / risk))
            pnl_per_share = (
                float(row["pnl_new"]) / float(row["quantity_new"])
                if float(row["quantity_new"]) > 0
                else 0.0
            )
            pnl = pnl_per_share * quantity
            exit_date = row["exit_date_new"]
            exit_reason = row["exit_reason_new"]
            marked_open = bool(row["marked_open"])
            executed = quantity > 0
        rows.append(
            {
                "signal_key": row["signal_key"],
                "symbol": row["symbol"],
                "rule": row["rule"],
                "entry_date": row["entry_date"],
                "exit_date": exit_date,
                "quantity": quantity,
                "pnl": pnl,
                "exit_reason": exit_reason,
                "marked_open": marked_open,
                "risk_multiplier": row["risk_multiplier"],
                "executed": executed,
            }
        )
    return pd.DataFrame(rows)


def curve(trades: pd.DataFrame, label: str) -> pd.DataFrame:
    executed = trades[trades["quantity"].gt(0) & trades["exit_date"].notna()].copy()
    executed["exit_day"] = pd.to_datetime(executed["exit_date"]).dt.normalize()
    days = pd.date_range(START, END, freq="B")
    daily = executed.groupby("exit_day")["pnl"].sum().reindex(days, fill_value=0.0)
    cumulative = daily.cumsum()
    peak = cumulative.cummax().clip(lower=0.0)
    return pd.DataFrame(
        {
            "date": days,
            "variant": label,
            "daily_pnl": daily.to_numpy(),
            "cumulative_pnl": cumulative.to_numpy(),
            "drawdown": (cumulative - peak).to_numpy(),
        }
    )


def summary(trades: pd.DataFrame, variant: str) -> dict[str, object]:
    executed = trades[trades["quantity"].gt(0) & trades["exit_date"].notna()].copy()
    pnl = executed["pnl"].astype(float)
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    variant_curve = curve(trades, variant)
    return {
        "variant": variant,
        "signals": len(trades),
        "executed": len(executed),
        "net_pnl": pnl.sum(),
        "profit_factor": gains / losses if losses > 0 else np.inf,
        "max_drawdown": variant_curve["drawdown"].min(),
        "win_rate_pct": 100.0 * pnl.gt(0).mean(),
        "marked_open": int(executed["marked_open"].sum()),
    }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = attach_v2_risk_multiplier(load_cluster_selected_trades())
    parent = build_parent_v2(selected)
    parent["executed"] = parent["quantity"].gt(0)
    current = build_v3(selected)
    curves = pd.concat(
        [curve(parent, "V2 parent shadow"), curve(current, "V3 current")],
        ignore_index=True,
    )
    summaries = pd.DataFrame(
        [summary(parent, "V2 parent shadow"), summary(current, "V3 current")]
    )
    trade_audit = pd.concat(
        [
            parent.assign(variant="V2 parent shadow"),
            current.assign(variant="V3 current"),
        ],
        ignore_index=True,
    )
    curves.to_csv(args.output_dir / "equity_curves.csv", index=False)
    summaries.to_csv(args.output_dir / "summary.csv", index=False)
    trade_audit.to_csv(args.output_dir / "trade_audit.csv", index=False)
    payload = {
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "series": {
            variant: [
                {
                    "date": pd.Timestamp(row.date).date().isoformat(),
                    "value": round(float(row.cumulative_pnl), 2),
                }
                for row in group.itertuples(index=False)
            ]
            for variant, group in curves.groupby("variant", sort=False)
        },
        "summary": json.loads(summaries.round(4).to_json(orient="records")),
        "amzn": json.loads(
            trade_audit[
                (trade_audit["variant"] == "V3 current")
                & (trade_audit["symbol"] == "AMZN")
                & (pd.to_datetime(trade_audit["entry_date"]).dt.normalize() == END)
            ][["entry_date", "exit_date", "pnl", "exit_reason", "marked_open"]]
            .round(4)
            .to_json(orient="records", date_format="iso")
        ),
    }
    (args.output_dir / "curve_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if args.html_output is not None:
        write_html_fragment(payload, args.html_output)
    print(summaries.round(2).to_string(index=False))
    print(f"selected_signals={len(selected)}")
    print(f"wrote={args.output_dir}")


if __name__ == "__main__":
    main()
