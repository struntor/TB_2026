# Pruefbericht: SPY Failed-Auction zu POC 2DTE Put-Fly/BWB

- Strategy ID: `spy-failed-auction-poc-2dte-bwb`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `backtest_validation`
- Empfohlener Arbeitszustand: `active`

## 1. Urteil in Kurzform

Die allgemeine Underlying-Hypothese hat 119 Failed-Auction-Signale, aber die handelbare 2DTE-Untermenge umfasst nur 16 Montag/Mittwoch-Signale. Deren Exit-Zeitpunkte und Legs sind lokal belegbar. `D:\Trading\volume_signal_research\trade-log(27).csv` wurde nachtraeglich gefunden und verifiziert den U10/L10-Wert `+476` sowie 10 Gewinner. Der Wert bleibt zu klein und zu stark sample-/variantengepraegt fuer eine Hochstufung.

## 2. Gepruefte Quellen

### Code

- `build_oo_spy_failed_auction_poc_put_bwb_2dte_batch.py`: Auswahl echter 2DTE-Montag/Mittwoch-Verfaelle, POC-Touch-Exit, Expiry-Fallback, drei Put-Legs.
- `analyze_spy_failed_auction_poc_takeprofit.py`: breiter Underlying-Statistiktest.

### Ergebnisartefakte

- `spy_failed_auction_poc_takeprofit_trades.csv`: 93 handelbare Next-Bar-Faelle aus 119 Signalen; 26 Pre-Entry-Hits separat.
- `spy_failed_auction_poc_takeprofit_stats.csv`: vier Underlying-Exitvarianten.
- `oo_spy_fa_poc_put_bwb_2dte_2024_2026_u10_l10_diagnostic.csv`: 16 Trades, 14 Touch/2 No-Touch.
- OptionOmega-Importdateien vorhanden; Ergebnislogs 23-27 liegen unter `D:\Trading\volume_signal_research`.

### Dokumentation

- `handoff_2026-07-08_spy_failed_auction_poc_bwb_optionomega.md`.

## 3. Tatsaechlich implementierte Strategie

- SPY oeffnet ueber prior-5-day VAH; nach 60 Minuten Acceptance zurueck in Value. Der alte OptionOmega-Builder verwendet den Zeitstempel des letzten bestaetigenden Bars als Entry und ist dadurch nicht kausal. Der neue Research-Pfad handelt am Open des folgenden 5-Minuten-Bars.
- Nur Montag/Mittwoch-Einstiege mit echtem 2DTE-Verfall im Options-Batch.
- Body-Put = gerundeter POC; oberer/lower Long Put nach Variantenbreite; `+1/-2/+1`.
- Exit beim ersten Bar, dessen High/Low den POC schneidet; sonst Verfall 16:00.
- Der Builder erzeugt nur OptionOmega-Imports, kein lokales Optionspreis-/Kostenmodell.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 119 Signale, 26 Pre-Entry-Hits, 93 Trades | Stats/Trades CSV | Zeilen und Stats geprueft | `artifact_verified` | bestaetigt |
| Underlying POC-Hit 68/93; inkl. Pre-Entry 79.0% | Stats CSV | Werte vorhanden | `artifact_verified` | bestaetigt |
| 16 2DTE-Signale, 14 Touch, 2 No-Touch | Diagnostic CSV | Exit-Reason-Gruppen geprueft | `artifact_verified` | bestaetigt |
| U10/L10 P/L `+476`, 10 Wins | `D:\Trading\volume_signal_research\trade-log(27).csv` | P/L, Prämien, Gebühren und Legs geprüft | `artifact_verified` | bestätigt |
| U15/L1 negativ `-152` | Handoff | lokales Ergebnislog fehlt | `documented` | nicht neu verifizierbar |

## 5. Methodische Risiken

- 16 Options-Trades sind eine sehr kleine, kalenderbedingt selektierte Teilstichprobe.
- Mehrere Breiten wurden bereits verglichen; weitere vorgeschlagene Grids erhoehen Multiple Testing.
- Underlying-Hit-Rate ist nicht gleich Optionsprofit; 14 Touches enthalten je nach Preis/Timing Verlusttrades.
- POC als Exit wird anhand der Bar-Range als beruehrt angenommen; reale Multi-Leg-Fillbarkeit zu diesem Zeitpunkt ist unbelegt.
- Kosten-/Slippage-Logik liegt im OptionOmega-Ergebnislog, nicht im alten Builder; der neue kalibrierte Proxy rekonstruiert sie explizit.
- 2024-2026 ist durch Variantenvergleich als Testfenster verbraucht.

## 6. Money Management und Portfolio

No-Touch-Fallback ist Trade-Management, nicht Portfolio-MM. Sizing, Kapitalbindung, Gap-/Expiry-Risiko, gleichzeitige Positionen und Verlustbudget fehlen.

## 7. IBKR-Integrationsreife

Kein Trade-Intent, Contract-Resolver, Dry-Run, Paper-Test, Combo-Order-Handling oder Restartschutz. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

- Die breite Studie meldet 73.1% Zielhits unter den 93 Trades; der Options-Batch 87.5% (14/16). Das ist unterschiedliche Kalenderselektion, kein direkt vergleichbarer Qualitaetssprung.
- U10/L10 ist lokal verifiziert; die Auswahl bleibt wegen mehrerer bereits getesteter Varianten von Multiple Testing betroffen.
- Einige alte Smoke-Dateien nutzen andere Breiten/Zeitraeume; nicht als Baseline mischen.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `backtest_validation`
- Empfohlene Stufe: `backtest_validation`
- Begruendung: technischer Options-Backtestpfad und Signalartefakte vorhanden, aber Ergebnislogs/Fillannahmen unvollstaendig und Stichprobe zu klein.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

`trade-log(27).csv` samt OptionOmega-Konfiguration wurde gegen die 16 Diagnostic-Zeilen verifiziert. Vor weiteren Varianten ist der kalibrierte U10/L10-Lauf als Kontrollanker einzufrieren.

## 11. Research-Update 2026-07-17

Ein eigener, reproduzierbarer Research-Pfad wurde unter
`D:\PycharmProjects\PythonProject\statistic\spy_failed_auction_poc_fly_research`
angelegt.

Gepruefte Neuerungen:

- kausaler Entry am Open des naechsten 5-Minuten-Bars;
- lokales Black-Scholes-Merton-Proxy-Pricing mit der letzten abgeschlossenen
  5-Minuten-VIX-Kerze als Flat-IV-Naeherung;
- explizite Gebuehren von 2 USD je Kontrakt und Seite;
- persistierte Run-Konfiguration, Trade-Ledger, Ausschluss-Ledger und
  Aggregatbericht ausserhalb des Repositories;
- vier mathematische Pricing-Smoke-Tests bestanden.

Ergebnis der U10/L10-Kontrollkonfiguration:

| Kennzahl | Kausaler lokaler Proxy | Alte OptionOmega-Referenz |
| --- | ---: | ---: |
| Kandidaten | 16 | 16 |
| Pre-Entry-POC-Hits | 2 | nicht getrennt |
| modellierte Trades | 14 | 16 |
| POC-Touch / Expiry | 12 / 2 | 14 / 2 |
| Gewinner | 7 | 10 |
| Netto-P/L | +680.96 USD | +476 USD |
| mittlerer Entry-Debit | 403.81 USD | ca. 434.56 USD dokumentiert |

Die beiden ausgeschlossenen Tage `2026-02-11` und `2026-02-23` stehen auch
im bereits vorhandenen Pre-Entry-Ledger. Sie sind keine verlorenen Daten,
sondern eine andere Hypothese: POC-Retest von unten statt erster Ruecklauf
zum POC.

Die P/L-Abweichung zu OptionOmega ist erwartbar und nicht als Verbesserung
zu interpretieren. Der lokale Proxy verwendet keine historische
SPY-IV-Smile, keinen Bid/Ask-Spread und andere Entry-Zeitpunkte. Er ist fuer
schnelle relative Variantenvergleiche geeignet, aber noch nicht fuer
Execution-P/L oder Produktionsentscheidungen.

Neuer minimaler naechster Test: historische SPY-Optionsquotes fuer einen
vorab festgelegten Teilzeitraum sichern und den Proxy gegen Entry-/Exit-Mids
sowie konservative Combo-Fills kalibrieren. Erst danach Exit-Regeln und
VIX-skalierte Breiten als getrennte Hypothesen testen.

## 12. Kalibrierungs-Update 2026-07-17

Das zuvor vermisste `D:\Trading\volume_signal_research\trade-log(27).csv`
wurde gefunden. Ein Download historischer SPY-Optionsdaten war fuer die
erste Kalibrierung daher nicht erforderlich.

Das Log enthaelt fuer alle 16 U10/L10-Trades:

- Entry- und Exit-Underlyingpreis;
- Prämie und Closing Cost;
- alle drei Entry-Legpreise;
- Opening-/Closing-Gebuehren;
- P/L und Exitgrund.

Die Gebuehrenrekonstruktion ist exakt: 2 USD je Kontrakt und Seite, also
8 USD beim Oeffnen und 8 USD beim Schliessen. Wertlos verfallene Positionen
haben keine Closing-Gebuehr. Die alte lokale Baseline hatte fuer zwei
Verfallsfaelle irrtuemlich je 8 USD Closing-Gebuehr belastet; der
unkalibrierte Kontrollwert wurde deshalb von +680.96 auf +696.96 USD
korrigiert.

Aus 48 Entry-Legpreisen wurden folgende mediane IV-Verhaeltnisse zum
zeitgleichen VIX abgeleitet:

| Leg | IV/VIX-Multiplikator |
| --- | ---: |
| oberer Long Put | 0.7818 |
| Body Short Put | 0.9013 |
| unterer Long Put | 1.1452 |

Das bildet den beobachteten Put-Skew ab. Eine anschliessende affine
Restkalibrierung wurde nicht anhand eines einzelnen Ergebnislaufs akzeptiert,
sondern fuer jeden Trade mit den jeweils anderen 15 Trades geschaetzt.

| Metrik auf denselben 16 OptionOmega-Trades | Flat VIX/BSM | Leave-one-out kalibriert |
| --- | ---: | ---: |
| P/L-MAE je Trade | 40.20 USD | 19.32 USD |
| P/L-RMSE je Trade | 59.43 USD | 25.61 USD |
| P/L-Korrelation | 0.9802 | 0.9780 |
| prognostizierter Gesamt-P/L | 838.28 USD | 511.12 USD |
| tatsächlicher Gesamt-P/L | 476.00 USD | 476.00 USD |
| prognostizierte Gewinner | 9 | 9 |
| tatsächliche Gewinner | 10 | 10 |

Die kalibrierte, kausale U10/L10-Baseline auf den 14 echten
Initial-Touch-Trades ergibt:

- 12 POC-Touch-Exits und 2 Verfallsfaelle;
- 7 Gewinner;
- +453.05 USD Netto-P/L;
- 437.71 USD mittlerer modellierter Entry-Debit;
- maximaler Drawdown -278.34 USD.

Dieser Wert ist nicht direkt mit den +476 USD aus OptionOmega zu
vergleichen, weil OptionOmega zwei zusaetzliche Retest-Faelle enthaelt und
am nicht kausalen Acceptance-Zeitstempel einsteigt.

Neuer minimaler naechster Schritt: U10/L10 Version 0.2.0 als Kontrollanker
einfrieren. Danach Exit-Regeln und VIX-skalierte Breiten getrennt testen.
Historische Optionsquotes werden nur fuer wenige vorab definierte Finalisten
geladen oder aus vorhandenen Dateien gelesen.

## 13. Strict-First-Touch-Korrektur 2026-07-17

Die Bezeichnung `Initial-Touch` in den Abschnitten 11 und 12 war zu weit
gefasst. Der damalige Guard schloss nur Entries am oder unter dem POC aus; er
pruefte nicht, ob ein frueherer Intraday-Bar den POC bereits beruehrt hatte.

Die unabhaengige Rohbar-Rekonstruktion trennt nun:

- 118 Hard-12-Acceptance-Signale;
- 44 ausfuehrbare Tage mit POC-Hit vor dem kausalen Entry;
- 3 Signale ohne folgenden 5-Minuten-Bar;
- 71 echte First-Touch-Einstiege;
- 48/71 POC-Touches bis Next+2 = 67,6 Prozent.

Die alte OptionOmega-Stichprobe enthaelt 13 strikte First-Touch- und 3
Retest-Faelle. Sie bleibt als Preis-/Legacy-Kontrollanker erhalten, darf aber
nicht mehr zur Schaetzung einer Forward-First-Touch-Wahrscheinlichkeit
verwendet werden.

Die getrennten Strategy-IDs
`spy-failed-auction-poc-hard12-first-touch-options` und
`spy-failed-auction-poc-hard6-first-touch-options` fuehren die neue Forschung
fort. Der vorliegende 2DTE-BWB-Eintrag wird nicht rueckwirkend umgedeutet.
