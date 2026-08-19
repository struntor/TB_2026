# Handoff 2026-07-01: Big Picture, Post-Break-Range und Seitwaertsphase

## Kontext

Wir haben nach dem Handoff vom 2026-06-30 weiter am Regelwerk gearbeitet.

Ausgangspunkt war AZO Ende Juni 2026:

- Am 2026-06-25/26 wurde im Live-/Kaltstart-Pfad zeitweise `bullish_tier3` erkannt.
- Charttechnisch war das falsch: Seit September 2025 grosses Bild bearish, nach dem Mai/Juni-Bruch eher Seitwaerts-/Repair-Range.
- Der vermeintliche Reclaim ueber `3055` lag fast exakt in der Mitte der Juni-Range.

Wichtige AZO-Werte:

- Tiefs:
  - 2026-06-01 low `2928.11`
  - 2026-06-22 low `2950.00`
- Hochs:
  - 2026-06-10 high `3177.12`
  - 2026-06-15 high `3170.12`
  - 2026-06-17 high `3165.51`
- Range-Mitte ca. `3053`
- 2026-06-25 decision price `3062.74`
- ATR14 ca. `89.43`
- Distanz zur Range-Mitte nur ca. `0.11 ATR`

Fachliche Einschaetzung:

- Nicht `bullish_tier3`.
- Nicht automatisch `no_trade` fuer alle Range-Situationen.
- Richtiger ist: Range/Value-Kontext erkennen und dann pro Regel entscheiden, ob ein Trade Sinn ergibt.

## Code-Aenderungen

Alle Code-Aenderungen liegen unter:

`D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03`

### `manual_regime_rules_backtest.py`

Neu/erweitert:

- `classify_big_picture_regime(...)`
  - Wochenstruktur aus abgeschlossenen Wochen.
  - Felder u. a.:
    - `big_picture_regime`
    - `big_picture_state`
    - `weekly_last_high`
    - `weekly_prev_high`
    - `weekly_last_low`
    - `weekly_prev_low`
    - `weekly_higher_high`
    - `weekly_higher_low`
    - `weekly_lower_high`
    - `weekly_lower_low`

- `detect_post_break_midrange(...)`
  - erkennt lokale Post-Break-Range ueber Swing-Extrema.
  - Felder u. a.:
    - `post_break_midrange`
    - `post_break_range_low`
    - `post_break_range_low_floor`
    - `post_break_range_latest_low`
    - `post_break_range_high`
    - `post_break_range_high_peak`
    - `post_break_range_mid`
    - `post_break_range_width_atr`
    - `post_break_mid_distance_atr`
    - `post_break_range_detail`

- `classify_intraday_value_profile(...)`
  - 10-Kalendertage-Intraday-Profil vor Decision-Zeit.
  - nutzt Volume, falls vorhanden, sonst TPO-Proxy.
  - Felder u. a.:
    - `profile_value_range`
    - `profile_poc`
    - `profile_value_low`
    - `profile_value_high`
    - `profile_value_width_atr`
    - `profile_price_location`
    - `profile_balance_score`
    - `profile_shape`
    - `profile_source`
  - Profilformen aktuell:
    - `d_profile`
    - `skewed_value_acceptance`
    - `unbalanced_or_trending`

- `classify_daily_structure_phase(...)`
  - kombiniert Post-Break-Kontext, Swing-Range und Value-Profil.
  - Felder:
    - `daily_structure_phase`
    - `range_basis`
    - `range_location`
  - Beispielwerte:
    - `post_break_range_middle`
    - `post_break_range_lower`
    - `post_break_range_upper`
    - `post_break_range_outside_down`
    - `post_break_range_outside_up`
    - `trend_or_unclassified`

Aktive Logik-Aenderungen:

- `post_break_midrange` blockiert falschen `bullish_reclaim`.
- `repair_breakout_priority` ignoriert Midrange-Reclaims.
- `bearish_gap_breakdown_continuation_short` wird bei `post_break_midrange` blockiert.
- `bearish_lower_high_failure_short` bleibt erlaubt.
- `bullish_crash_repair_reversal_long` bleibt erlaubt.

Wichtig: Die getestete generische Range-Long-Regel wurde wieder entfernt.

### Weitere Skripte

Neu:

- `build_topn_union_ledger_sequential.py`
  - sequenzieller Ledger-Build fuer TopN-Union oder einzelne Span-Variante.
  - vermeidet Windows-`ProcessPoolExecutor`/Pipe-Probleme.
  - kann per `--variant cutoff_2025-11_top50` exakt die 50 Assets einer Variante bauen.

- `annotate_trades_big_picture.py`
  - annotiert vorhandene Trades mit Big-Picture-Kontext.

- `analyze_top50_big_picture_overlay.py`
  - wertet Top50-Trades nach `big_picture_regime` und einfachen Szenarien aus.

- `find_post_break_midrange_trade_deltas.py`
  - vergleicht alte Ledger-Trades gegen neue Logik und listet Trade-Deltas.

Erweitert:

- `build_trade_ledger.py`
  - kann Big-Picture-Spalten in Ledger mergen.

- `simulate_selected_symbols_portfolio_mm.py`
  - reicht optionale Big-Picture-Felder in Portfolio-Trades durch.

## Wichtige Testergebnisse

### AZO Diagnose

Nach der neuen Erkennung:

- AZO 2026-06-25:
  - `regime = bearish_countermove`
  - `big_picture_regime = bearish_primary`
  - `post_break_midrange = True`
  - `profile_value_range = True`
  - `profile_shape = skewed_value_acceptance`
  - `daily_structure_phase = post_break_range_middle`
  - `range_basis = swing_extremes+value_profile`
  - `bullish_reclaim = False`
  - `baseline_signal = no_trade`

Damit ist der falsche `bullish_tier3` entfernt, ohne pauschal alle Range-Trades zu blockieren.

### Kontrollfaelle

INTU 2024-10-16:

- Range-Kontext erkannt.
- `bearish_lower_high_failure_short` bleibt erlaubt.
- Alter Trade bleibt:
  - Points `+10.25`

AGQ 2024-06-05:

- Range-Mitte erkannt.
- Alter `bearish_gap_breakdown_continuation_short` aus Range-Mitte wird blockiert.
- Alter Trade war Verlierer:
  - Points ca. `-0.936`

KLAC 2024-09-19:

- Range-Kontext erkannt.
- `bullish_crash_repair_reversal_long` bleibt erlaubt.
- Alter Trade bleibt:
  - Points ca. `+37.13`

## Negativer Test: generischer Range-Mid-Long

Wir haben testweise eine Regel `post_break_range_mid_long` gebaut:

- Entry bei `post_break_midrange`
- Stop unter jungem Range-Low
- Target Range-High bzw. naechster Widerstand
- Size `0.5`

Breiter Test auf Top50-Union/live-history-548d:

- 110 ausgefuehrte Range-Mid-Long-Trades
- 19 Symbole
- Gesamt-PnL ca. `-2,215.55 USD`

Problematische Symbole:

- CEG ca. `-862`
- LLY ca. `-743`
- DG ca. `-696`
- BLDR ca. `-678`
- HUM ca. `-601`
- ORCL ca. `-594`
- TER ca. `-593`
- ELF ca. `-589`

Fazit:

- Range-Mid-Long ist als generische Regel kein Edge.
- Idee bleibt fachlich fuer einzelne Setups plausibel, aber nicht als breite automatische Regel.
- Regel wurde aus aktiver Engine entfernt.

## Big-Picture-Overlay Ergebnis

Ein simples Gate wie "keine Longs in bearish weekly picture" war negativ.

Live-History 548d, Forward-Sample:

- all: ca. `+16,406 USD`
- block_longs_in_bearish_primary: ca. `+13,923 USD`
- block_longs_in_bearish_or_pressure: ca. `+6,170 USD`
- primary_aligned_only: ca. `+2,846 USD`

Fazit:

- Wochenebene ist diagnostisch wertvoll.
- Aber ein pauschales Big-Picture-Gate zerstoert profitable Repair-/Countertrend-Trades.

## Test auf exakt `cutoff_2025-11_top50`

Die Liste enthaelt exakt 50 Assets:

`SPX, REGN, BLK, MLM, MSCI, LLY, INTU, MANH, CEG, SAIA, IVV, HUBB, GRMN, DLTR, ETN, MCK, AMZN, HUM, SRE, BX, ALV, LYV, TER, ORCL, GPC, BLDR, AMD, KLAC, V, VTI, DG, OEF, QQQ, SITM, LMT, ECL, TT, DIA, MOH, ELF, EFX, HLT, GLD, CVNA, SPY, CDW, VOO, LHX, IWB, UAL`

Neu gebaut:

- `trade_ledger_cutoff_2025_11_top50_live_history_548d_phase_test.csv`
- `cutoff_2025_11_top50_live_history_548d_phase_test_*`

Vergleich gegen alten `cutoff_2025-11_top50` Live-History-548d:

- Net Profit alt: ca. `34,403.88`
- Net Profit neu: ca. `34,489.13`
- Delta: ca. `+85.25 USD`
- Trades alt: `1090`
- Trades neu: `1089`
- Winrate alt: ca. `61.28%`
- Winrate neu: ca. `61.34%`
- Profit Factor alt: ca. `1.938`
- Profit Factor neu: ca. `1.943`
- Max Drawdown unveraendert: ca. `-1082.07`

Forward ab Cutoff:

- Alt: ca. `4,863.20`
- Neu: ca. `4,864.87`
- Delta: ca. `+1.66`

Trade-Deltas:

- MOH 2025-12-02:
  - alter `bearish_gap_breakdown_continuation_short` entfernt
  - alter PnL ca. `-95.49 USD`
  - neue Phase: `post_break_range_middle`
  - neues Signal: `no_trade`
- QQQ 2025-11-21:
  - alter `bearish_gap_breakdown_continuation_short` entfernt
  - alter PnL ca. `-83.59 USD`
  - neue Phase: `post_break_range_outside_down`
  - neues Signal: `no_trade`
- QQQ 2025-11-28:
  - neuer `bearish_failed_countermove_short_baseline`
  - neuer PnL ca. `-93.82 USD`
  - Phase: `trend_or_unclassified`

Phasenstatistik im neuen 50er-Ledger:

- `trend_or_unclassified`: ca. `+5797.75` points / 766 trades
- `post_break_range_middle`: ca. `+405.07` points / 171 trades
- `post_break_range_upper`: ca. `+316.48` points / 139 trades
- `post_break_range_lower`: ca. `-196.19` points / 147 trades
- `post_break_range_outside_down`: ca. `-51.12` points / 26 trades
- `post_break_range_outside_up`: ca. `+31.96` points / 17 trades

Interpretation:

- Neue Seitwaerts-/Range-Erkennung ist nicht invasiv und leicht positiv.
- `post_break_range_middle` ist nicht pauschal schlecht.
- `post_break_range_lower` sieht aktuell am schwaechsten aus und ist der naechste Untersuchungs-Kandidat.

## Aktueller fachlicher Stand

Wir wollen nicht direkt neue Trade-Regeln erzwingen.

Ziel ist zuerst:

1. Marktphase sauber erkennen.
2. Bestehende Regeln je Phase auswerten.
3. Danach gezielt Regeln gaten, degradieren, sizing aendern oder neue Regeln einfuehren.

Aktuelle Phasenlogik ist ein erster Schritt:

- grosses Bild: `big_picture_regime`
- Tagesphase: `daily_structure_phase`
- Range-Basis: `range_basis`
- Profilkontext: `profile_*`

Wichtiges Prinzip:

- Range ist nicht automatisch `no_trade`.
- Range-Mitte ist kein Breakout/Reclaim.
- Range-Rejection- und Repair-Trades koennen weiter valide sein.

## Naechstes Todo

Als naechstes `post_break_range_lower` untersuchen.

Warum:

- Im 2025-11-Top50-Test schwach:
  - ca. `-196.19` points
  - 147 Trades
- Fachlich koennen dort sehr unterschiedliche Situationen stecken:
  - echte Lower-Reversal-Longs
  - fallendes Messer
  - Breakdown-Continuation kurz vor Range-Bruch
  - Support-Test ohne Bestaetigung

Konkreter naechster Arbeitsschritt:

1. Aus `trade_ledger_cutoff_2025_11_top50_live_history_548d_phase_test.csv` alle Trades mit:
   - `daily_structure_phase = post_break_range_lower`
2. Nach Regel gruppieren:
   - Punkte
   - Winrate
   - Long/Short getrennt
   - `profile_price_location`
   - `range_basis`
3. Drei bis fuenf konkrete Charts ansehen:
   - groesste Gewinner
   - groesste Verlierer
   - je ein Long/Short-Fall
4. Entscheiden, ob `post_break_range_lower` weiter unterteilt werden muss:
   - `lower_reversal_confirmed`
   - `lower_breakdown_attempt`
   - `lower_value_acceptance`
   - `falling_knife`

Nuetzliche Commands:

```powershell
python -c "import pandas as pd; df=pd.read_csv(r'.\PythonProject\statistic\spx_adx_di_5min_2024_02_03\trade_ledger_cutoff_2025_11_top50_live_history_548d_phase_test.csv',parse_dates=['entry_date']); x=df[df['daily_structure_phase'].eq('post_break_range_lower')]; print(x.groupby(['rule','side']).agg(trades=('symbol','size'),points=('points','sum'),avg_points=('points','mean'),wins=('points',lambda s:(s>0).sum())).sort_values('points').round(2).to_string())"
```

```powershell
python .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\build_topn_union_ledger_sequential.py --variant cutoff_2025-11_top50 --output .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\trade_ledger_cutoff_2025_11_top50_live_history_548d_phase_test.csv
```

```powershell
python .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\compare_cutoff_forward_topn_variants.py --ledger .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\trade_ledger_cutoff_2025_11_top50_live_history_548d_phase_test.csv --top-n 50 --cutoffs 2025-11 --output-prefix cutoff_2025_11_top50_live_history_548d_phase_test
```

## 2026-07-04 - Aktive Research-Baseline nach Damage-Cluster-Arbeit

Die aktive Research-Baseline wurde nach Top50-/Top100-Validierung erweitert.

Neue Baseline-Kette:

1. bisherige Current Baseline inkl. Gap-Fix, LHF Entry-Vetos und prior-churn warning
2. `failed_countermove_weak_retest_veto`
3. `hard_lhf_cluster_veto_lb2_loss5_net350`

### failed_countermove_weak_retest_veto

Trade wird uebersprungen, wenn alle Bedingungen gelten:

```text
rule == bearish_failed_countermove_short_baseline
side == short
daily_structure_phase_fresh == trend_or_unclassified
volume_price_phase == normal_volume_trend
bearish_countermove_subregime == bearish_countermove_weak_retest
```

### hard_lhf_cluster_veto_lb2_loss5_net350

Portfolio-level Hard-Veto. Neuer Trade wird uebersprungen, wenn alle Bedingungen gelten:

```text
new_trade.rule == bearish_lower_high_failure_short
new_trade.side == short
in den letzten 2 Kalendertagen vor entry_date:
  mindestens 5 geschlossene Core-Short-Verluste
  und Core-Short-Netto-PnL <= -350 USD
```

Core-Short-Regeln:

```text
bearish_gap_breakdown_continuation_short
bearish_lower_high_failure_short
bearish_failed_countermove_short_baseline
```

Wichtig: Die Cluster-Logik ist kausal. Uebersprungene Trades zaehlen spaeter nicht als geschlossene Verluste, die weitere Vetos ausloesen.

### Validierung

Top50 fixed basket:

```text
Net:            37,206.08 -> 37,828.86  (+622.78)
Forward:         5,299.15 ->  5,376.72  (+77.57)
Profit Factor:       2.153 ->     2.208
Max Drawdown:      -914.29 ->   -840.15
```

Top100 fixed basket:

```text
Net:            57,474.21 -> 60,359.39  (+2,885.18)
Forward:         5,446.22 ->  6,091.07  (+644.85)
Profit Factor:       1.806 ->     1.893
Max Drawdown:    -1,922.97 -> -1,579.37
Rolling50 worst: -1,246.18 -> -1,226.78
```

Reproduzierbare Artefakte:

```text
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\build_baseline_with_hard_lhf_cluster_veto.py
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\current_baseline_rule_stack_2026_07_04.md
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\current_baseline_rule_stack_2026_07_04.json
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\baseline_with_hard_lhf_cluster_veto_summary.csv
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\top50_baseline_hard_lhf_cluster_veto_trades.csv
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\top100_baseline_hard_lhf_cluster_veto_trades.csv
```

Naechster sinnvoller Schritt:

```text
Nicht weiter an der Cluster-Schwelle drehen.
Stattdessen die neue Baseline gegen weitere Cutoff-Baskets / Walk-forward-Sichten pruefen
oder die verbleibenden groessten Verlustcluster nach dieser Baseline neu clustern.
```
