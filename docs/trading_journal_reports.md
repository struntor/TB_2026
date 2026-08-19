# Trading-Journal lesen und Equity-Reports erzeugen

Das Journal ist die gemeinsame, unveränderliche Datenbasis für Backtest,
Paper-Trading und späteren Live-Handel. Der Reportgenerator liest die
SQLite-Datei ausschließlich read-only und verändert weder Trades noch Läufe.

Standarddatenbank:

```text
D:\Trading\journal\trading_journal.sqlite
```

## Was eine Equity-Kurve hier bedeutet

Eine Reportkurve ist die realisierte Strategiekurve:

```text
Equity = Startkapital + Summe(realized_pnl aller im Zeitraum geschlossenen Trades)
```

Sie ist **nicht** der Kontowert aus der TWS. Offene Positionen, unrealisierte
Gewinne/Verluste, Einzahlungen und Trades anderer Strategien werden nicht
einbezogen. Ein Trade gehört zu einem Monat oder Jahr, wenn sein `closed_at`
in New-York-Zeit in diesem Zeitraum liegt.

## Verfügbare Strategien anzeigen

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe -m src.journal.report `
  --list-strategies
```

Die Ausgabe zeigt `strategy_id`, `variant_id`, Version, Laufart und den letzten
Lauf. Für reproduzierbare Auswertungen sollte bevorzugt die unveränderliche
`variant_id` verwendet werden. `strategy_id` fasst dagegen alle Versionen einer
Strategiefamilie zusammen.

## Monatsreport

Beispiel für Juli 2026 der Manual-Regime-Version 1.0.0:

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe -m src.journal.report `
  --variant-id manual-regime-baseline@1.0.0 `
  --period 2026-07 `
  --actual-run-types paper
```

## Jahresreport

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe -m src.journal.report `
  --variant-id manual-regime-baseline@1.0.0 `
  --period 2026 `
  --actual-run-types paper,live
```

Ohne `--output-dir` wird der Report unter
`D:\Trading\journal\reports\<Strategie>\<Zeitraum>` abgelegt.

## Referenz-Backtest und Startkapital festlegen

Standardmäßig wird der jüngste abgeschlossene Backtest derselben Strategie oder
Variante verwendet. Für einen vollständig reproduzierbaren Vergleich kann die
Run-ID fest vorgegeben werden:

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe -m src.journal.report `
  --variant-id manual-regime-baseline@1.0.0 `
  --period 2026 `
  --backtest-run-id "backtest:manual-regime-baseline@1.0.0:..." `
  --starting-capital 10000
```

Die explizite Backtest-Run-ID muss zur ausgewählten Strategie gehören. Fehlt
`--starting-capital`, wird `parameters_json.starting_capital` des Backtests
verwendet; ist es dort ebenfalls nicht vorhanden, startet die Kurve bei null.

## Backtest gegen Paper/Live lesen

`backtest_vs_actual.csv` verbindet beide Welten über den stabilen
`signal_key`. Dieser Schlüssel bezeichnet dieselbe fachliche Handelsentscheidung
in verschiedenen Läufen.

| `match_status` | Bedeutung |
| --- | --- |
| `matched` | Signal existiert im Referenz-Backtest und im Paper/Live-Journal |
| `backtest_only` | Modelltrade ohne tatsächlich journalisierten Trade |
| `actual_only` | tatsächlicher Trade ohne Gegenstück im gewählten Backtest |

Alle Differenzspalten rechnen immer:

```text
actual - backtest
```

Damit bedeutet beispielsweise `pnl_difference = -20`, dass Paper/Live beim
gleichen Signal 20 USD schlechter als der Backtest abgeschnitten hat. Der
Report bricht bewusst ab, wenn derselbe `signal_key` mehrfach in den gewählten
Paper/Live-Läufen vorkommt; eine Mehrfachausführung darf nicht stillschweigend
verrechnet werden.

Wichtig für Monatsreports: Schließt ein korrespondierender Backtest- und
Paper/Live-Trade in unterschiedlichen Monaten, erscheint er in den jeweiligen
Monatsreports als einseitig. Der Jahresreport führt ihn wieder zusammen, sofern
beide Schlusszeitpunkte im Jahr liegen.

## Erzeugte Dateien

| Datei | Inhalt |
| --- | --- |
| `report.html` | direkt lesbare Zusammenfassung mit Grafik und Signalvergleich |
| `equity.png` | Equity, kumuliertes P&L und kumulierte Abweichung gematchter Trades |
| `summary.json` | Kennzahlen und exakte Auswahlparameter für Automatisierung |
| `backtest_equity.csv` | Datenpunkte der Backtestkurve |
| `actual_equity.csv` | Datenpunkte der Paper/Live-Kurve |
| `backtest_trades.csv` | geschlossene Backtesttrades des Zeitraums |
| `actual_trades.csv` | geschlossene Paper/Live-Trades des Zeitraums |
| `backtest_vs_actual.csv` | Signal-für-Signal-Abgleich und Differenzen |
| `matched_pnl_difference.csv` | zeitlicher Verlauf der kumulierten P&L-Abweichung gematchter Signale |

Die HTML-Seite zeigt höchstens die ersten 100 Vergleichszeilen, während die CSV
immer den vollständigen Abgleich enthält.

## Offene Modellpositionen am Datenende

`end_of_test` ist kein Handels-Exit. Eine Position, die am Ende eines täglichen
Backtest-Laufs weder Stop, Target noch eine reguläre Exit-Regel erreicht hat,
wird im Journal deshalb so gespeichert:

```text
status = open
closed_at_utc = null
exit_price = null
realized_pnl = null
metadata.carry_forward = true
metadata.snapshot_reason = end_of_data
```

Der letzte Kurs, Zeitpunkt und unrealisierte P&L bleiben ausschließlich als
Snapshot in den Metadaten erhalten. Der folgende Paper-/Live-Lauf lädt diese
offenen Modellpositionen aus dem explizit ausgewählten Modellhistorien-Lauf.
Der Decision-Faktor `model_open_position_guard` blockiert jedes neue Signal für
dasselbe Symbol, bis der kontinuierliche Modellpfad einen echten Exit erzeugt.

Ältere Journal-Läufe, in denen `end_of_test` noch als `status=closed` gespeichert
wurde, werden beim Laden als `legacy_end_of_test` erkannt und ebenfalls wie
offene Shadow-Positionen behandelt. Sie zählen nicht als realisierte Trades und
nicht als Gewinne oder Verluste eines Recent-Loss-Filters.

## Welche Tabellen sind beim Nachforschen wichtig?

- `strategy_runs`: welcher Lauf, welche Strategieversion und welche Laufart;
- `signal_decisions` und `decision_factors`: warum ein Signal entstanden oder
  verworfen worden ist;
- `trades` und `trade_legs`: geplanter beziehungsweise realisierter Trade und
  seine Aktien-, Options- oder Combo-Beine;
- `orders`, `executions`, `commissions`: was IBKR tatsächlich angenommen und
  ausgeführt hat;
- `reconciliation`: explizite Zuordnung eines Paper/Live-Trades zur
  Backtestreferenz.

Für regelmäßige Berichte kann derselbe Befehl nach dem morgendlichen TWS-Sync
und Catch-up-Backtest ausgeführt werden. Da der Generator read-only arbeitet,
kann er auch laufen, während der 24/7-Supervisor das Journal verwendet.

## LHF 1R-EOD-Regel und Shadow-Trade

Im Paper-Trading aktiviert der Tages-Orchestrator automatisch die
Profit-Protection für Aktien-Shorts mit der Entry-Regel
`bearish_lower_high_failure_short`. Nach Abschluss der 15:50-ET-Kerze wird der
offene Gewinn mit dem tatsächlichen Entry und dem ursprünglichen Stop berechnet:

```text
R = (Entry - Schlusskurs 15:50) / (ursprünglicher Stop - Entry)
```

Bei mindestens `+1.00R` werden die ursprünglichen TP/SL-Orders sicher storniert
und ab 15:55 ET eine Market-Buy-Order zum Schließen übermittelt. Diese Erweiterung
ist hart auf `DU`-Paper-Konten begrenzt; der Live-Account-Pfad aktiviert sie nicht.
Der Journal-Event heißt `lhf_eod_profit_protection_1r`.

Der „Shadow-Trade“ ist keine zweite Brokerposition. Nach dem echten 1R-EOD-Exit
führt der Monitor die ursprünglichen Stop-/Target-Grenzen virtuell auf
abgeschlossenen 5-Minuten-Kerzen fort. Berührt eine Kerze beide Grenzen, wird wie
im Backtest konservativ der Stop angenommen. Beim virtuellen Ende schreibt er
`lhf_shadow_baseline_closed` mit `managed_r`, `shadow_r`, `delta_r` sowie den
vergleichbaren Brutto-P&L-Werten in USD ins Journal.
Der persistente Zustand liegt standardmäßig hier:

```text
D:\Trading\state\manual_regime_lhf_eod_1r_shadow_v1.json
```

Nach mindestens fünf abgeschlossenen Vergleichen blockiert ein Kill-Switch neue
1R-EOD-Exits, wenn die kumulierte Shadow-Differenz höchstens `-2R` beträgt.

## LHF Cluster-Choice-Score (V2 Base, V1 Shadow)

Für gleichzeitige `bearish_lower_high_failure_short`-Signale im routbaren Teil
des korrelierten Clusters `IVV, OEF, SPY, VOO, VTI` berechnet die Live-Pipeline ab zwei
Kandidaten einen kausalen Auswahl-Score:

```text
choice_score = (Entry - Target) / (Stop - Entry)
```

Das entspricht dem beim Einstieg geplanten CRV des Shorts. Der höchste Wert
erhält Rang 1; bei exakt gleichem Wert gilt die feste Liquiditätsreihenfolge
`SPY, IVV, VOO, VTI, OEF`. Die Entscheidung wird als Faktor
`lhf_cluster_choice_crv_top1_v1` mit Rang, Kandidatenliste und den
diagnostischen Strukturkomponenten journalisiert.

Seit `manual-regime-baseline@2.0.0` wird nur Rang 1 geroutet. Die anderen
ansonsten zulässigen Kandidaten werden als V1-Shadow dokumentiert, erzeugen
aber keine zweite Brokerposition. Die historische Forschungsanalyse liegt unter:

```text
D:\Trading\journal\research\lhf_cluster_rank_v1
```

## V2 Regime-/Volumen-Positionsgröße und V1-Shadow

Die Paper-Baseline `manual-regime-baseline@2.0.0` verwendet weiterhin dieselben
Entry-, Stop- und Target-Regeln wie V1. Nur die Risikozuweisung wird kausal aus
vollständig abgeschlossenen SPX-/SPY-Sitzungen vor dem Einstieg bestimmt:

```text
Struktur-Score 70, anfällige Regel  -> 75 %
Struktur-Score 100, anfällige Regel -> 50 %
Cluster-Short mit Volumenrisiko     -> höchstens 50 %
```

Die Reduktionen werden nicht unter 50 Prozent miteinander multipliziert. Bei
fehlenden erforderlichen Kontextdaten gilt für den betroffenen Trade ebenfalls
die konservative 50-Prozent-Grenze.

Der Faktor `regime_volume_position_sizing_v1` enthält unter anderem
`base_quantity`, `legacy_shadow_quantity`, Struktur-Score, Volumenprofil-Kontext
und die konkreten Reduktionsgründe. Dieselben Daten werden in den Trade-Metadaten
und in `manual_regime_live_trades_YYYYMMDD.csv` gespeichert. Die V1-Shadow-
Stückzahl ist rein virtuell und wird nicht an IBKR gesendet.

## V3 frischer Failed-Countermove-Stop und V2-Shadow

Die Paper-Baseline `manual-regime-baseline@3.0.0` ändert ausschließlich die
Stop-Logik von `bearish_failed_countermove_short_baseline`. Entry-Zeitpunkt und
Target bleiben unverändert. Der neue Stop wird um 09:40 ET ausschließlich aus
bereits bekannten Daten berechnet:

```text
max(
  V2-Stop,
  Hoch des Vortags 12:00-15:55 ET + ATR-Toleranz,
  Hoch der 09:30- und 09:35-Kerze + ATR-Toleranz
)
```

Sinkt das geplante CRV danach unter `1.0`, wird der Trade nicht geroutet. Der
Faktor `failed_countermove_fresh_structure_stop_v1` protokolliert alle drei
Stop-Komponenten, Risiko, Reward, CRV und das Veto. `legacy_v2_stop` und
`fresh_stop_parent_v2_quantity` halten den alten V2-Plan als virtuellen Shadow
fest; es wird keine zweite Brokerposition eröffnet.

Der kausale Vergleich der 100 historischen Signale bis einschließlich
2026-08-12 liegt unter:

```text
D:\Trading\journal\research\failed_countermove_fresh_confirmation
```

Die gewählte Variante `fresh_stop_structure_only` erzielte im Test 2.357,06 USD
gegenüber 2.202,28 USD, erhöhte den Profit-Faktor von 1,38 auf 1,47 und reduzierte
den maximalen Drawdown von -1.179,87 USD auf -908,17 USD. Der Vorteil ist jedoch
nicht in jedem Kalenderjahr vorhanden; deshalb muss V2 als Parent-Shadow weiter
auswertbar bleiben. Varianten mit verzögerter Opening-Bestätigung wurden wegen
deutlich negativer Gesamtergebnisse verworfen.

## V4 Viertelrisiko für Gap-Breakdown und V3-Shadow

Die Paper-Baseline `manual-regime-baseline@4.0.0` reduziert ausschließlich
ausgewählte Signale von `bearish_gap_breakdown_continuation_short` auf 25 Prozent
der von V3 vorgesehenen Größe. Der kombinierte Filter wird nur aus Informationen
abgeschlossener Handelssitzungen vor dem Einstieg berechnet:

```text
high_volume_breakout
ODER high_volume_breakdown bei SPX != strong_bull
ODER SPX > +2 ATR über 20 Sitzungen und relative Stärke des Assets >= 0
```

Die tatsächlich gesendete Stückzahl wird nach Anwendung des Faktors `0.25` auf
ganze Aktien abgerundet. Der Decision-Faktor
`gap_breakdown_regime_volume_quarter_sizing_v1` und die Trade-Metadaten enthalten
den Auslöser, SPX-Richtung, 20-Tage-Relative-Stärke, V4-Stückzahl sowie
`v3_parent_shadow_quantity`. Diese V3-Shadow-Stückzahl ist rein virtuell und
erzeugt keinen zweiten Brokerauftrag.
