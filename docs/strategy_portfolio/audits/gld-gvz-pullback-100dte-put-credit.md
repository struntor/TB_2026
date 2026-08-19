# Pruefbericht: GLD GVZ-Pullback 100DTE Put-Credit

- Strategy ID: `gld-gvz-pullback-100dte-put-credit`
- Audit-Datum: `2026-08-11`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `backtest_validation`
- Empfohlener Arbeitszustand: `frozen_candidate`

## 1. Urteil in Kurzform

GLD/GVZ wird als eingefrorener Research-Kandidat aufgenommen. Der strenge
ThetaData-Replay umfasst 14 handelbare Strukturen, 13 Gewinner, einen Verlust,
+596,80 USD netto und -638,30 USD taeglichen MTM-MaxDD. Ein einzelner Verlust
verbraucht etwa fuenf bis sechs normale Gewinner; der nachtraeglich eingefuehrte
q90-Krisenfilter benoetigt deshalb unabhaengige Forward-Evidenz.

## 2. Gepruefte Quellen

- `research/test_commodity_vol_pullback_100dte_proxy.py`
- `research/test_commodity_vol_pullback_crisis_guards_proxy.py`
- `research/verify_commodity_vol_pullback_entry_quotes_thetadata.py`
- `research/backtest_gld_gvz_pullback_100dte_thetadata.py`
- `D:/Trading/Rohdaten/Rohdaten_GVZ.csv`
- `D:/Trading/Rohdaten/Rohdaten_GLD.csv`
- Exact-Ledger: `D:/PycharmProjects/PythonProject/data/option_omega_15DTE_ruleset1_analysis/gld_gvz_pullback_100dte_thetadata_v1/trades.csv`

## 3. Tatsaechlich getestete Strategie

- GVZ ueber dem vorherigen rollierenden q60 und mindestens 4 % Rueckgang vom
  Peak innerhalb von zehn Sitzungen; 25 Kalendertage Cooldown.
- Positives 20-Sitzungen-GLD-Momentum, GLD ueber vorheriger SMA200.
- Krisengate: Entry-GVZ nicht ueber dem vorherigen rollierenden q90.
- Entscheidung `13:35 ET`; lokale GVZ-Balken `12:35 CT`.
- Erster gelisteter Standard-Monatsverfall ab 100 DTE, Short-Delta
  `-0,20 +/- 0,02`, zehn Punkte Breite und ein Kontrakt.
- TP60 und qualitaetsgepruefter Zeitexit bei 50 Rest-DTE.

## 4. Ergebnispruefung

| Kennzahl | Wert |
| --- | ---: |
| Kausale Signale nach q90-Gate | 19 |
| Vollstaendige Exact-Strukturen | 14 |
| Gewinner | 13 |
| Netto-P/L | +596,80 USD |
| Schlimmster Trade | -476,60 USD |
| Taeglicher MTM-MaxDD | -638,30 USD |
| Maximales Einzelrisiko | 896,30 USD |
| Maximales gleichzeitiges Risiko | 1.707,60 USD |
| Durchschnittliche Haltedauer | 28,50 Tage |

Die Jahreswerte nach Entry sind 2023 +57,60, 2024 +303,00, 2025 +712,80 und
2026 -476,60 USD. Alle Gewinner erreichen TP60; der einzige Verlust wird bei
50 Rest-DTE geschlossen.

## 5. Methodische Risiken

- Das q90-Krisengate wurde nach Sichtung dreier schwerer 2026-Proxyverluste
  eingefuehrt und ist post-hoc.
- Nur 14 Exact-Trades und nur ein Verlust; die Tail-Wahrscheinlichkeit ist
  unpraezise.
- Fuenf der 19 Signale besitzen kein vollstaendiges gemeinsam quotiertes Paar
  innerhalb der Delta-Toleranz.
- GVZ faellt nach 60 Sitzungen nur in 54,17 % der Basissignale; ein Teil des
  Ergebnisses stammt eher aus GLD-Richtung und Theta als aus sicherer
  Volatilitaetsnormalisierung.

## 6. Money Management und Portfolio

Die zehn Punkte Breite begrenzen das definierte Einzelrisiko auf unter 900 USD.
Historisch sind hoechstens zwei Positionen mit zusammen 1.707,60 USD offen und
damit unter dem bestehenden 2.500-USD-Optionsrisikodeckel. Ein zusaetzlicher
Stop wurde nicht nachtraeglich auf den einzigen Verlust optimiert.

## 7. IBKR-Integrationsreife

Keine Botintegration vorhanden. Der Kandidat ist `frozen_candidate`, aber nicht
produktionsberechtigt. Ein Forward-Scanner darf Signale und Ketten erfassen;
eine automatische Orderausloesung ist noch nicht gerechtfertigt.

## 8. Widersprueche und offene Punkte

Der ungefilterte gemeinsame Proxy ist wegen drei Verlusten 2026 negativ, waehrend
die q90-Variante positiv ist. Diese Verbesserung ist fachlich als Vermeidung
eines extremen Krisenregimes plausibel, aber nicht unabhaengig bestaetigt.
Offen sind reale Forward-Quotes und ein neuer Verlustfall unter dem eingefrorenen
Gate.

## 9. Reifegradentscheidung

- Stufe: `backtest_validation`
- Arbeitszustand: `frozen_candidate`
- Audit-Evidenz: `reproduced`
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Den unveraenderten Regelstack als Forward-Paper-Scanner journalisieren. Keine
weitere Schwellen- oder Exitwahl auf den vorhandenen 14 Trades. Nach ausreichend
neuen Signalen werden Quote-Abdeckung, TP-/Zeitexit und Tail-Verhalten neu
bewertet.
