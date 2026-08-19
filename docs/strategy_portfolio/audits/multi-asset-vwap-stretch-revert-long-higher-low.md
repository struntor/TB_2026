# Pruefbericht: Multi-Asset VWAP Stretch-Revert Long / Higher-Low Entry

- Strategy ID: `multi-asset-vwap-stretch-revert-long-higher-low`
- Parent: `multi-asset-vwap-stretch-revert-long`
- Audit-Datum: `2026-07-30`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `backtest_validation`
- Produktionsberechtigt: `false`

## Regel

Das unveraenderte Stretch-Signal entsteht nach Tages-Close. Der unmittelbare Folgetag muss ueber seinem Open und ueber dem Signal-Close schliessen und ein hoeheres Tief als der Signaltag bilden. Erst am Open des darauffolgenden Handelstags wird eingestiegen. Stop, Target, Haltedauer, kausaler Vortrags-RVWAP, Gap-Behandlung, RS60- und Normalvolumenfilter entsprechen der korrigierten Parent-Strategie. Der Filter wird vor der Trade-Simulation angewendet.

## Reproduziertes Ergebnis

Zeitraum 2025 bis 29.07.2026, 10 bps je Seite:

- 157 Trades
- +12,3823R
- Profit Factor 1,2724
- MaxDD -7,5957R
- 2025: +11,8934R
- 01.01.-10.04.2026: -3,4385R
- 11.04.-29.07.2026: +3,9274R

Die korrigierte Next-Open-Kontrolle erzielt 325 Trades und +36,5001R, aber einen wesentlich groesseren MaxDD von -24,5697R. Die Higher-Low-Regel reduziert damit Risiko und Frequenz deutlich, verbessert jedoch nicht in jedem Teilzeitraum den Erwartungswert.

## Urteil

Die Variante ist der einzige getestete restriktivere Einstieg mit sinnvoller Gesamtbeziehung aus Ertrag und Drawdown. Sie wurde jedoch unter Kenntnis aller bisherigen Zeitraeume ausgewaehlt und besitzt keinen unangetasteten OOS-Nachweis. Sie wird deshalb ab 30.07.2026 unveraendert als separater Forward-Kandidat gefuehrt. Keine Bot-Integration und keine weitere Parameteroptimierung vor neuer Evidenz.

## Artefakte

- Runner: `D:\PycharmProjects\PythonProject\research\test_vwap_stretch_revert_long_entry_confirmation.py`
- Summary: `D:\Trading\research\multi_asset_vwap_stretch_revert_long\frozen_oos_v1\entry_confirmation_v1\summary.csv`
- Trades: `D:\Trading\research\multi_asset_vwap_stretch_revert_long\frozen_oos_v1\entry_confirmation_v1\confirmed_higher_low_reversal_trades.csv`
- Plot: `D:\Trading\research\multi_asset_vwap_stretch_revert_long\frozen_oos_v1\entry_confirmation_v1\entry_confirmation_equity.png`
