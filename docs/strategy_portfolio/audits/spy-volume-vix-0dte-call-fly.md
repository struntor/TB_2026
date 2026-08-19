# Pruefbericht: SPY Volume/VIX 0DTE Call-Fly

- Strategy ID: `spy-volume-vix-0dte-call-fly`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `exploration`
- Empfohlener Arbeitszustand: `active`

## 1. Urteil in Kurzform

Vorhanden ist ein sauberer All-Days-Signal-/Import-Builder, noch kein Ergebnis-Backtest. 868 SPY-Sessions und 2604 OptionOmega-Legzeilen sind lokal belegt. Trotz des Namens wird VIX derzeit nicht geladen oder verwendet; die Strategie ist aktuell ein fixer ATR-verschobener 0DTE-Call-Fly mit Volumenprofil-Kontext.

## 2. Gepruefte Quellen

### Code

- `statistic/spy_volume_vix_research_part2/build_spy_baseline.py`: Datenvalidierung, prior ATR14, echtes volumen-gewichtetes Profil, All-Day-Kontext und OptionOmega-Legs.
- `research_config.py`: Zeitraum, Entry/Exit, Center und Breite.

### Ergebnisartefakte

- `outputs/spy_all_days_fixed_width_context_2023_2026.csv`: 868 Sessions, 2023-01-03 bis 2026-06-18, Profil- und Strikekontext.
- `outputs/oo_spy_call_fly_w2_center_m0p25atr_2023_2026.csv`: 2604 Zeilen = drei Legs je Session.
- Kein Trade-/P/L-Ergebnisartefakt gefunden.

### Dokumentation

- `README.md` im Research-Ordner.

## 3. Tatsaechlich implementierte Strategie

- SPY, jeder Handelstag; Entry 09:35-Bar-Close, Exit 16:00, 0DTE.
- Symmetrischer Call-Fly `+1/-2/+1`.
- Center = 09:35-Close minus 0.25 vorherige ATR14, auf 1-Punkt-Raster; Breite +/-2 SPY-Punkte.
- Prior-10-Kalendertage-Volumenprofil wird aus echtem SPY-Barvolumen gebildet, ist aber nur Kontext und kein Filter.
- VIX-variable Breite ist ausdruecklich nicht implementiert. Kein Optionspreis-, Fill-, Fee- oder P/L-Modell im lokalen Builder.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| All-Days-Baseline 2023-2026 erzeugt | Context CSV | 868 eindeutige Sessions, erste/letzte Session geprueft | `artifact_verified` | bestaetigt |
| Drei Legs pro Tag | OptionOmega CSV | 2604 = 3 x 868 | `artifact_verified` | bestaetigt |
| Echtes Volumenprofil | Code/Context | `profile_source=true_volume`, positive Volumenfelder im Vertrag | `artifact_verified` | Builder und Artefakt vorhanden |
| Profitabilitaet/Drawdown | keine Quelle | kein OptionOmega-Ergebnis oder lokales P/L-Ledger | `unknown` | nicht getestet/belegt |
| VIX-Effekt | keine Implementierung | VIX wird nicht geladen | `unknown` | noch keine VIX-Strategie |

## 5. Methodische Risiken

- Kein Performance-Ergebnis; Signal-/Import-Erzeugung allein ist keine statistische Evidenz.
- Center und +/-2-Breite sind aus einer SPX-Analogie abgeleitet, nicht fuer SPY validiert.
- Entry zum 09:35-Bar-Close kann in 5-Minuten-Daten erst nach Abschluss des Bars bekannt sein; Orderzeit/Fill muss auf den naechsten handelbaren Zeitpunkt verschoben oder exakt definiert werden.
- Profil-Buckets verteilen Barvolumen gleichmaessig ueber High-Low; das ist eine Approximation, kein Price-at-Volume-Print.
- Keine Optionsliquiditaet, Quotes, Slippage, Fees oder fehlende Strikes untersucht.

## 6. Money Management und Portfolio

Nicht begonnen. Weder Kontraktzahl noch Debitlimit, Max-Loss-Budget, Intraday-Exit oder Portfolio-Exposure sind getestet.

## 7. IBKR-Integrationsreife

Nur OptionOmega-Importvertrag vorhanden. Kein IBKR-Trade-Intent, Contract-Resolver, Quote-/Combo-Dry-Run, Paper-Test, Gate oder Restartschutz. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

- Name und Hypothese nennen Volume/VIX; implementiert ist Volume-Kontext ohne Filter und ohne VIX.
- README sagt Entry 09:35-Close, waehrend reale Handelbarkeit erst nach Beobachtung dieses Closes geklaert werden muss.
- Outputdateinamen enden 2023_2026; der konkrete letzte Tag ist 2026-06-18.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `exploration`
- Empfohlene Stufe: `exploration`
- Begruendung: technisch validierter Dataset-/Import-Builder, aber keinerlei P/L- oder VIX-Evidenz.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Den fixen, ungefilterten Baseline-Import einmal mit eingefrorenen OptionOmega-Kosten/Fills auswerten und das vollstaendige Trade-Log archivieren; vorab nur Plausibilitaetsgrenzen fuer Tradezahl, Debit und fehlende Quotes definieren, noch keine VIX- oder Profilselektion.

## 11. Ergebnisnachtrag 2026-07-30

Der angeforderte Kontrollanker wurde inzwischen mit echten historischen SPY-Quotes statt mit einem Optionspreis-Proxy gerechnet. Einstieg ist kausal um 09:40 nach dem abgeschlossenen 09:35-Bar; konservative Bewertung kauft beide Long-Legs zum Ask und verkauft die zwei Body-Calls zum Bid. Der Verfall wird intrinsisch aus dem SPY-Tagesschluss bewertet. Angesetzt sind 2 USD Opening Fee je Kontrakt beziehungsweise 8 USD je Fly.

- 868 Kontexttage, davon 864 mit vollstaendiger Struktur und vier mit fehlendem Strike.
- Ungefiltert konservativ: -8.453 USD, 30,56 Prozent Gewinner, PF 0,70.
- Ungefiltert Midpoint: -3.194 USD nach Gebuehren, PF 0,87.
- Midpoint vor Gebuehren: +3.718 USD beziehungsweise nur rund +4,30 USD je Trade.
- Konservativ ist der Fly bereits vor Gebuehren mit -1.541 USD negativ.
- Entwicklung: 2023-2024; unangetasteter Testblock: 2025-01-01 bis 2026-06-18.

Vor dem OOS-Open wurde genau eine Volume/VIX-Hypothese festgelegt: Preis oberhalb der Value Area, 09:35-Volumen hoechstens 80 Prozent des vorherigen 20-Tage-Medians und VIX hoechstens 20. Im Testblock verbleiben 30 Trades mit -226 USD konservativ beziehungsweise -14 USD am Midpoint nach Gebuehren. Die Hypothese ist damit nicht bestaetigt.

Artefakte liegen unter `D:\Trading\research\spy_volume_vix_0dte_call_fly\v1`. Die Strategie wird geparkt. Eine Wiederaufnahme ist nur als fachlich neue, vorab definierte breitere beziehungsweise VIX-skalierte Struktur mit neuem Forward-Fenster sinnvoll; weitere Filterwahl auf diesem OOS-Zeitraum waere Datenanpassung.
