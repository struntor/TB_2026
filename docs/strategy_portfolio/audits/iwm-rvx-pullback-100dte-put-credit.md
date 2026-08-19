# Pruefbericht: IWM RVX-Pullback 100DTE Put-Credit

- Strategy ID: `iwm-rvx-pullback-100dte-put-credit`
- Audit-Datum: `2026-08-11`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `backtest_validation`
- Empfohlener Arbeitszustand: `frozen_candidate`

## 1. Urteil in Kurzform

Der RVX/IWM-Kandidat ist als eigene Strategie in der Liste aufgenommen. Alle zehn
historischen Signale besitzen nach Feiertags- und Joint-Pair-Aufloesung echte,
vollstaendige ThetaData-Bid/Ask-Pfade. Der Replay ergibt zehn Gewinner, +580,40
USD netto und -277,30 USD taeglichen MTM-MaxDD. Das ist aussichtsreich, aber wegen
der kleinen, erst Ende 2023 beginnenden und teilweise post-hoc gefilterten
Stichprobe noch keine Bot- oder Produktionsfreigabe.

## 2. Gepruefte Quellen

- `research/test_multi_asset_vol_pullback_signal_frequency.py`
- `research/backtest_multi_asset_vol_pullback_100dte_proxy.py`
- `research/test_multi_asset_vol_pullback_management_proxy.py`
- `research/analyze_multi_asset_vol_pullback_forward_paths.py`
- `research/verify_iwm_rvx_pullback_entry_quotes_thetadata.py`
- `research/resolve_iwm_rvx_pullback_data_gaps.py`
- `research/backtest_iwm_rvx_pullback_100dte_thetadata.py`
- `research/test_iwm_rvx_pullback_parameter_stability_proxy.py`
- Exact-Tradeledger: `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\iwm_rvx_pullback_100dte_thetadata_v1\trades.csv`

## 3. Tatsaechlich getestete Strategie

- RVX oberhalb seines vorherigen rollierenden 60%-Quantils.
- Mindestens 4 % Rueckgang vom RVX-Peak innerhalb von zehn Sitzungen.
- 25 Kalendertage Cooldown, positives IWM-20-Sitzungen-Momentum und IWM oberhalb
  der vorherigen SMA200.
- Lokale RVX-Daten bei `12:35 CT = 13:35 ET`, IWM bei `13:35 ET`.
- Erster tatsaechlich gelisteter Standard-Monatsverfall ab 100 DTE, Short-Delta
  circa -0,20, zehn Punkte Breite, ein Kontrakt.
- TP60 und qualitaetsgepruefter Zeitexit bei 50 Rest-DTE.
- Entry-Credit konservativ als `Short Bid - Long Ask`.

## 4. Ergebnispruefung

| Kennzahl | Wert |
| --- | ---: |
| Geschlossene Trades | 10 |
| Gewinner | 10 |
| Netto-P/L | +580,40 USD |
| Schlechtester realisierter Trade | +9,40 USD |
| Taeglicher MTM-MaxDD | -277,30 USD |
| Durchschnittliche Haltedauer | 51,50 Tage |
| Credit-Spanne | 1,24 bis 1,48 USD |
| Definiertes Einzelrisiko inkl. Opening Fees | 855,30 bis 879,30 USD |

Der generische Proxy bleibt zudem in der unmittelbaren Signal-Nachbarschaft aus
RVX-Quantilen 55/60/65 % und Drops 3/4/5 % in allen neun Kombinationen positiv.
Nach 60 Sitzungen liegt IWM in 7 von 9 auswertbaren Faellen hoeher (Median
+4,30 %), RVX in 8 von 9 Faellen niedriger (Median -2,16 Punkte).

## 5. Methodische Risiken

- Die Exact-Stichprobe beginnt erst Ende 2023 und enthaelt keinen echten
  Baerenmarktblock.
- Der SMA200-Filter ist fachlich plausibel, wurde aber nach Sichtung der schwachen
  ungefilterten 2022-Ergebnisse ergaenzt und ist damit post-hoc.
- Zehn Gewinner schaetzen die wahre Trefferquote nicht verlaesslich.
- VIX/XSP, VXN/QQQ und RVX/IWM sind untereinander noch deutlich korreliert und
  bilden keine vollstaendige Diversifikation.

## 6. Money Management und Portfolio

Die zehn Punkte Breite begrenzen den nominellen Maximalverlust je Kontrakt auf
unter 900 USD. TP60 und 50-DTE-Zeitexit sind getestet; der historische Replay
hat maximal zwei ueberlappende Positionen und bleibt unter dem bestehenden
2.500-USD-Optionsrisikodeckel. Eine verbindliche gemeinsame Portfolioallokation
mit den bereits laufenden Optionsstrategien ist noch nicht implementiert.

## 7. IBKR-Integrationsreife

Keine Bot- oder Orderintegration vorhanden. Der Kandidat ist im Strategieregister
als `frozen_candidate` aufgenommen, `production_eligible=false`. Vor einem
Orderpfad muss mindestens ein unveraenderter Forward-Paperblock vorliegen.

## 8. Datenluecken und offene Punkte

Die beiden anfaenglichen Datenluecken wurden ohne Proxy-Fills aufgeloest:

- Am 2026-02-06 war der kalendarische dritte Freitag 2026-06-19 wegen
  Juneteenth nicht gelistet. Der tatsaechliche Monatsverfall ist Donnerstag,
  2026-06-18. Das 235/225-Paar hat Short-Delta -0,1882 und 1,35 USD Credit.
- Am 2024-08-29 hatte das theoretische 202/192-Paar keinen historischen Quote
  fuer das Long-Leg. Das naechste gemeinsam quotierte Paar innerhalb der
  Delta-Toleranz ist 200/190 mit Short-Delta -0,1853 und 1,24 USD Credit.

Offen bleibt ausschliesslich die unabhaengige zeitliche Bestaetigung. VXN/QQQ
wird vorerst nicht weiterverfolgt, weil Proxy und Volatilitaetsnormalisierung
deutlich schwaecher sind.

## 9. Reifegradentscheidung

- Stufe: `backtest_validation`
- Arbeitszustand: `frozen_candidate`
- Audit-Evidenz: `reproduced`
- Produktionsberechtigt: `false`

Die Regeln werden ab jetzt nicht anhand derselben zehn Trades weiter optimiert.

## 10. Naechster minimaler Test

Den unveraenderten Signal- und Strukturstack taeglich als Forward-Paper-Scanner
ausfuehren und Signal, tatsaechliche IWM-Kette, Credit, Greeks und theoretischen
Managementpfad journalisieren. Erst nach unabhaengiger Evidenz ist eine
orderausloesende Bot-Integration zu entscheiden.
