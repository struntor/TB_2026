# Pruefbericht: SPY Failed-Auction Hard-12 First-Touch Options

- Strategy ID: `spy-failed-auction-poc-hard12-first-touch-options`
- Audit-Datum: `2026-07-18`
- Audit-Typ: `causal_reconstruction_proxy_and_historical_nbbo_validation`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `bot_integration` / `paper_only`

## 1. Urteil in Kurzform

Die breite Underlying-Hypothese ist kausal reproduziert, aber die fruehere Aussage von rund 81 Prozent war keine handelbare Forward-Wahrscheinlichkeit. Die breite CRV10-Variante bleibt verworfen. Eine nachfolgende, vorab eng definierte Teilhypothese liefert jedoch einen Paper-Kandidaten: Hard-12 plus kausaler RVOL 1,00-1,20, 1DTE U10/L10 mit Body am POC, genau ein Fly und kein CRV10-Gate. Auf 12 unveraenderten Signalen erreicht der konservative Touch-Mid-30m-Exit durchschnittlich +51,42 USD; die Stichprobe ist fuer eine Produktionsfreigabe zu klein.

## 2. Gepruefte Quellen

- `D:\Trading\research\spy_failed_auction_poc_fly\signal_heatmaps\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\hard_signal_option_grid_initial_touch_v2\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\vix_scaled_geometry\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\exit_policy_sensitivity\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\u10_l10_1dte_real_quotes\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\u10_l10_1dte_crv10_otm_selector\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\u10_l10_2dte_crv10_otm_selector\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\u10_l10_crv10_1dte_vs_2dte\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\one_dte_exit_mechanics_v1\REPORT.md`
- Research-Code unter `D:\PycharmProjects\PythonProject\statistic\spy_failed_auction_poc_fly_research`

## 3. Tatsaechlich implementierte Strategie

SPY oeffnet ueber prior-5 VAH. Nach 12 aufeinanderfolgenden 5-Minuten-Closes innerhalb der prior-5 Value Area wird am Open des folgenden Bars eingestiegen. Bis zum bestaetigten Signal darf kein Bar-Low den POC beruehrt haben. Getestet wurden 1-3 Kalender-DTE, Body-Offsets -4 bis +4, symmetrische und Broken-Wing-Flys sowie mehrere kausale Exit-Zeitpunkte.

## 4. Ergebnispruefung

- 118 Hard-12-Signale exakt gegen das Legacy-Ledger reconciled.
- 71 strikte Initial-Touch-Einstiege; 48 POC-Touches bis Next+2.
- Distanz unter 0,50 Prozent: 32/38 Touches; ab 0,50 Prozent: 16/33.
- 525 Optionszellen; nach drei Zeitbloecken, Nachbarschaft und 22,63-USD-Proxyfehler bestehen 44 Zellen, ausschliesslich 1DTE.
- Hard-12 1DTE U10/L10 am POC: 46 Proxy-Trades, durchschnittlich +46,70 USD; schlechtester Zeitblock +36,24 USD.
- Historische Contract-Metadaten fuer alle 71 Signale: 51 haben einen exakt am Folgetag gelisteten SPY-Verfall; alle drei U10/L10-Strikes sind vorhanden.
- U10/L10 Body am POC: 51 echte Quote-Replays; bester konservativer Reward/Risk 4,95:1, daher 0/51 mit CRV mindestens 10:1.
- Naechster OTM-Body mit CRV mindestens 10:1: 51/51 selektierbar, median 11 Punkte unter POC; durchschnittlich -5,35 USD konservativ und -1,45 USD am Mid.
- Nur 30/51 POC-Touches vor 1DTE-Verfall; Touch-Trades durchschnittlich +37,50 USD konservativ, Nicht-Touch-Trades -66,57 USD.
- Exakter 2DTE-Selector: 37/37 selektierbar, durchschnittlich +0,70 USD konservativ; Development -7,63 USD, Validation +12,35 USD, OOS 2026 H1 -15,50 USD.
- Matched 1DTE/2DTE auf 28 Tagen: 2DTE-Vorteil nur +0,71 USD konservativ; gepaartes Bootstrap-95-Prozent-Intervall -18,21 bis +20,57 USD.
- VIX-skalierte Breiten schlagen fixe Breiten auf Familienebene nicht.
- Keine Exit-Alternative ist zugleich full-/matched- und zeitblockrobust.

## 5. Methodische Risiken

Der breite 1-3-DTE-Proxy extrapoliert weiterhin eine 2DTE-Kalibrierung. Fuer den fixierten 1DTE-U10/L10-Test liegen nun echte historische NBBO-Zustaende vor, jedoch keine garantierten Combo-Fills. Konservative Werte kreuzen jede Leg einzeln am Bid/Ask und sind eine harte Untergrenze; dass auch der OTM-Mid leicht negativ ist, kann aber nicht durch Spreads erklaert werden. Der matched-DTE-Vergleich enthaelt nur 17 Termine und keinen 2021-2022-Fall. Multiple Testing bleibt trotz Nachbarschafts- und Fehlergates relevant.

## 6. Money Management und Portfolio

Sizing, Maximalverlustbudget, gleichzeitige Positionen, Korrelation zum Aktienbot, Gap-Risiko und Kapitalbindung fehlen. Ein positiver Proxy-Durchschnitt ist kein Money-Management-Nachweis.

## 7. IBKR-Integrationsreife

Seit 2026-07-18 implementiert:

- kausaler Live-Evaluator fuer Hard-12 und RVOL 1,00-1,20,
- exakter Resolver fuer den naechsten Kalendertag-Verfall und die drei SPY-Put-Strikes,
- BAG-Entry als 1/-2/1-Limitorder fuer genau einen Fly,
- persistenter Restartzustand mit OrderRef-, OrderId- und ConId-Pruefung,
- Journalmanifest, DecisionRecord, BAG-Trade-Intent, Optionslegs und Execution-Ingest,
- bestaetigter POC-Touch erst nach Abschluss der 5-Minuten-Kerze,
- Closing-BAG-Limit am Combo-Mid und Preisamendierung nach 30 Minuten,
- harter DU-Paperkonto-Guard, unabhaengig vom allgemeinen Botmodus.

Der Pfad ist optional in `live_manual_regime_tws_bot.py` eingebunden. Unit-, Journal- und Kompilierungstests sind gruen. Ein TWS-Dry-Run und ein echter Paper-Fill stehen noch aus; am Audit-Tag lief keine TWS. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

Die alte 2DTE-OptionOmega-Stichprobe mischt First-Touch- und Retest-Faelle und nutzt einen nicht kausalen Entry-Zeitstempel. Full-DTE-Kohorten enthalten unterschiedliche Wochentage; nur die 28 matched-DTE-Zeilen sind direkte DTE-Vergleiche. Der historische 1DTE-U10/L10-Test zeigt einen Horizontfehler: 67,6 Prozent gelten bis Next+2, waehrend vor dem 1DTE-Verfall nur 58,8 Prozent der Quote-Kohorte den POC erreichen. 2DTE erhoeht die Touchrate, erzwingt fuer CRV 10:1 aber einen weiter entfernten Body und verbessert den gepaarten Erwartungswert nicht materiell.

## 9. Reifegradentscheidung

- Empfohlene Stufe: `bot_integration`
- Arbeitszustand: `frozen_candidate`
- Produktionsberechtigt: `false`
- Begruendung: Die alte CRV10-Familie bleibt negativ. Der neue RVOL-1,00-1,20-Kandidat ist in allen drei Zeitbloecken positiv, aber mit nur 12 historischen Trades und ohne beobachtete BAG-Fills ausschliesslich fuer Paper-Trading geeignet.

## 10. Naechster minimaler Test

An einem Handelstag zuerst ohne `--place-orders` gegen die Paper-TWS starten. Danach mit explizitem `--place-orders` den ersten seltenen Signaltrade abwarten und Entry-Limit, tatsaechlichen BAG-Fill, Touchzeit, Exit-Limit, 30-Minuten-Amendierung und Journal-Reconciliation pruefen. Bis dahin keine Parameterveraenderung.
