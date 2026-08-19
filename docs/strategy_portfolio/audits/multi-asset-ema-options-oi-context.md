# Pruefbericht: Multi-Asset EMA Long plus Options/OI Context

- Strategy ID: `multi-asset-ema-options-oi-context`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `statistical_evidence`
- Empfohlener Arbeitszustand: `parked`

## 1. Urteil in Kurzform

Code und Artefakte belegen einen explorativen EMA-Long-Test mit Options-/OI-Filtern und eine separate OI-Flow-Eventstudie. Die aggregierte Strategietabelle enthaelt jedoch nur sieben statt der im Report genannten zehn Assets; Optionsvarianten teilweise nur sechs. Es gibt keine Train/OOS-Trennung. Der scheinbar beste Filter (`iv_put_filters`, +19,59R) ist auf demselben Zeitraum 2025-01-02 bis 2026-05-19 ausgewaehlt, den er bewertet. OI-Flow ist ueber zehn Assets vorhanden, aber Vorzeichen und Effektgroessen sind stark assetabhaengig. Der Registry-Status ist angemessen, nicht integrationsreif.

## 2. Gepruefte Quellen

### Code

- `research/msft_ema_options_strategy_research.py`: EMA20/50, ATR14, Next-Open-Entry, 1R-Ziel, Options-/OI-Filter.
- `research/multi_asset_ema_options_strategy_research.py`: Schleife ueber zehn Symbole und Aggregation vorhandener Symbol-Summaries.
- `research/multi_asset_options_oi_flow_research.py`: OI-Flow fuer zehn Assets; t/t-1-Features.
- `research/analyze_multi_asset_oi_flow_results.py`: aggregierte Event-/Korrelationsinterpretation.

### Ergebnisartefakte

- `D:/Trading/optimizer/options_data/thetadata/multi_asset_ema_options_2025_2026/multi_asset_variant_summary.csv`: 12 Varianten; nur 6-7 Symbole.
- `.../multi_asset_symbol_variant_summary.csv`: MSFT, AAPL, NVDA, JPM, XOM, WMT, AMZN; keine Strategy-Summary fuer TSLA/UNH/CAT.
- `.../oi_flow_analysis/multi_asset_oi_flow_event_summary.csv`: zehn Assets, 1/3/5/10-Tage-Effekte.
- `.../oi_flow_analysis/multi_asset_oi_flow_correlation_summary.csv`.

### Dokumentation

- `ai_agents/2026-05-28/stand_thetadata_msft_options_client_2026-05-28.md`
- generierte Reports im genannten Aggregatordner.

## 3. Tatsaechlich implementierte Strategie

- Daily Long bei EMA20 > EMA50; Signal am Tag t, Entry zum Open t+1.
- Stop 2x ATR; Ziel 1R; konservative Same-Day-Reihenfolge: Stop vor Target; EMA-Cross-Exit zum Folge-Open.
- Filter: IV < 25 %, Put-/Call-Struktur, OI-Flow; Optionsvarianten laufen nur an Tagen mit ATM-IV.
- OI-Flow wird vor Verwendung um einen Tag verschoben. Die Baseline laeuft dagegen auf allen Preistagen; Varianten haben somit unterschiedliche Zeitabdeckung.
- Keine Transaktionskosten, Slippage, Gaps durch Stops oder Portfolio-Positionsueberlappung modelliert.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| Zehn Assets in Strategietest | Report/ASSETS | Summary-Dateien geprueft | `conflict` | nur 7 Baseline-, 6 IV-Symbole; TSLA/UNH/CAT fehlen |
| `iv_put_filters` +19,59R, 66 Trades | Variant-Summary | CSV gelesen | `artifact_verified` | bestaetigt fuer 6 Symbole |
| Baseline +13,57R, 175 Trades | Variant-Summary | CSV gelesen | `artifact_verified` | bestaetigt fuer 7 Symbole |
| OI-Flow universell schwach/assetabhaengig | Event-/Corr-Summary | zehn Asset-Effekte gelesen | `artifact_verified` | bestaetigt; z.B. Hedge-Build 10D im Mittel -0,07 %-Pkt., Vorzeichenanteil positiv 70 % |
| OOS 2026 offen | Code/Artefakte | ein zusammenhaengender Zeitraum, keine Splitlogik | `artifact_verified` | keine OOS-Trennung vorhanden |

## 5. Methodische Risiken

- Varianten und Schwellen werden auf dem gesamten 2025-2026-Zeitraum bewertet; kein Train/Test/Walk-forward.
- Aggregiertes `sum_r` summiert Symbolresultate ohne Kalender-/Kapitalaggregation; `avg_max_drawdown_r` ist kein Portfolio-MaxDD.
- Unterschiedliche Datenverfuegbarkeit zwischen Varianten und Symbolen erzeugt Selektions-/Coverage-Bias.
- Zehn Assets wurden vorgegeben, aber nur sieben Strategieausgaben existieren; fehlende Assets koennen das Ranking verzerren.
- Keine Kosten, Slippage, Gap-Fills oder gleichzeitige Exposure-Begrenzung.
- OI ist kein kausaler Beweis fuer Support/Resistance; Eventstudien enthalten ueberlappende Forward-Returns und keine Signifikanz-/Multiple-Test-Korrektur.

## 6. Money Management und Portfolio

ATR-Risiko und 1R-Ziel sind Trade-Level-Management. Portfolio-Sizing, gleichzeitige Positionen, Sektor-/Beta-Konzentration und Optionsdaten-Ausfallregeln fehlen.

## 7. IBKR-Integrationsreife

Kein Trade-Intent-Vertrag, Adapter, Replay/Paper-Test oder Order-Gate belegt. Die Strategie nutzt Forschungsdateien ausserhalb des Bot-Projekts.

## 8. Widersprueche und offene Punkte

- Report sagt zehn Assets; Strategieaggregat hat sieben, IV-Varianten sechs.
- Registry nennt saubere Train-2025/OOS-2026-Trennung als offen; tatsaechlich wurde 2026 bereits im gemeinsamen Selektionszeitraum betrachtet und ist damit nicht mehr unangetastet.
- OI-Flow-Studie umfasst zehn Assets, Strategie-Filtertest nicht.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `statistical_evidence`
- Empfohlene Stufe: `statistical_evidence`
- Begruendung: reproduzierbare Artefakte und plausible Kausalitaetsvorsicht, aber kein belastbarer OOS-Nachweis.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Eine einzige vorab festgelegte Variante (`iv_put_filters`) gegen die Baseline auf einem neuen, fuer beide Varianten identischen Kalenderfenster und identischem Sieben-Asset-Universum testen; fehlende Optionsfeatures als explizite No-Trade-/Missing-Data-Kategorie ausweisen.
