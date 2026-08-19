# Pruefbericht: VIX-Reversal 100DTE Put-Credit

- Strategy ID: `vix-reversal-100dte-put-credit`
- Audit-Datum: `2026-08-11`
- Audit-Typ: `code_artifact_and_paper_integration_review`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `bot_integration`
- Empfohlener Arbeitszustand: `monitoring`

## 1. Urteil in Kurzform

Der urspruengliche seltene SPX-Panik-Reversal-Ansatz war nicht handelbar. Die
spaetere, separat definierte XSP-VIX-Pullback-Variante ist dagegen als kleiner
Paper-Kandidat integriert: zehn Punkte definiertes Risiko, striktes Delta- und
Quote-Gate, TP60, 60-DTE-Zeitstopp und 2.500 USD Portfoliocap. Die
Produktions-Signalmaschine reproduziert alle historischen Research-Signale und
die reale TWS liefert eine passende handelbare XSP-Struktur. Wegen kleiner
Stichprobe und negativer 2022-Teilperiode bleibt die Strategie
`bot_integration/monitoring`, nicht Produktion.

## 2. Gepruefte Quellen

### Code

- `research/test_100dte_vix_reversal_put_credit.py`: Spike/Drop-Signal, 20-Tage-Cooldown, Delta -0,10, Width 100, Roll bei ca. 60DTE.
- `research/test_100dte_high_vix_put_credit.py`: High-VIX-Vergleichsbasis.

### Ergebnisartefakte

- `.../thetadata_100dte_vix_reversal_put_credit/vix_reversal_100dte_put_credit_trades.csv`: 2024-/2025-Einzeltrades und viele duplizierte Parameterzuordnungen.
- `.../vix_reversal_100dte_put_credit_summary.csv`: nur positive-Credit-Trades.
- `.../thetadata_100dte_high_vix_put_credit/high_vix_100dte_put_credit_summary.csv`.

### Dokumentation

- `ai_agents/strategy_ideas/vix_reversal_100dte_put_credit_2026-06-11.md`.

## 3. Tatsaechlich implementierte Strategie

- VIX um 13:35; Spike-Schwellen 30/35/40, Drop 5/10/15 Punkte, Mindest-Entry-VIX 20/25.
- Nach Signal 100DTE SPX Put-Credit: Short ca. -0,10 Delta, Long 100 Punkte tiefer.
- Exit um 60DTE oder am Sample-Ende; konservative Bid/Ask- und Mid-PnL.
- `COOLDOWN_DAYS=20`, zusaetzlich keine Ueberlappung bis Exit.
- Summary skaliert auf Credit-Budgets 100/250/500; negative oder Null-Credits werden still ausgeschlossen.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| High-VIX allein 2025 negativ | High-VIX-Summary | CSV gelesen | `artifact_verified` | VIX>20: -454,87; VIX>25: -250,89 bei Budget 100 |
| 2025 Drop15-Trade +470 USD 1x | Trade-CSV | Zeile 2025-04-14 gelesen | `artifact_verified` | bestaetigt |
| 2025 frueher Reversal-Trade -180 USD 1x | Trade-CSV | Zeile 2025-04-09 gelesen | `artifact_verified` | bestaetigt; Entry ist netto Debit (-0,9) |
| Summary zeigt Strategieprofit | Summary-Code/CSV | Filter `entry_credit_cons > 0` geprueft | `conflict` | negativer Trade wird ausgeschlossen; Summary ist kein vollstaendiger Signaltest |
| Mehrere Schwellen bestaetigen Robustheit | Trade-CSV | gleiche Events mehrfach parametrisiert | `conflict` | keine unabhaengigen Beobachtungen |

## 5. Methodische Risiken

- Nur ein VIX-Event je Sample; keine statistische Aussage.
- Parametergrid ueber 18 Signalregeln und drei Budgets auf denselben zwei Panikepisoden.
- Summary konditioniert nachtraeglich auf positiven Credit; das ist nur dann zulaessig, wenn `credit > 0` explizite, vorab definierte Entry-Regel ist.
- Sample-End-Mark kann vom geplanten 60DTE-Exit abweichen.
- Keine Kommission, Liquiditaets-/Quote-Qualitaetsregel oder Margin-/Mark-to-market-Betrachtung.

## 6. Money Management und Portfolio

Credit-Budget-Skalierung ist kein ausreichendes Money-Management. Max Loss, Margin, Stop/Profit-Take, Crash-Gap und Portfolio-Korrelation fehlen.

## 7. IBKR-Integrationsreife

Kein Trade-Intent, Adapter, Replay/Paper-Test, Order-Gate oder Restart-Schutz. Nicht integrationsreif.

## 8. Widersprueche und offene Punkte

- Bericht nennt den negativen 2025-Trade korrekt; Summary entfernt ihn.
- `spike>=30/35/40` erzeugt beim Peak 49,12 denselben Trade und darf nicht als drei Bestaetigungen gezaehlt werden.
- „OOS“ umfasst je nur eine Episode und wurde fuer Regelinterpretation genutzt.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `idea`
- Empfohlene Stufe: `idea`
- Begruendung: technische Machbarkeit gezeigt, aber Summary-Bias und praktisch keine Stichprobe.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Summary korrigieren, sodass jedes Signalereignis einschliesslich nichtpositiver Credits ausgewiesen wird; dann eine einzige vorab definierte Regel (`Spike>=35`, Drop>=15, Entry-VIX>=25, Credit>0 als explizites Entry-Gate) auf einer historischen Panikepisodenliste auswerten, ohne weitere Parameterwahl.

## 11. Ergebnis des vorab begrenzten Quick-Probes vom 2026-08-09

Der minimale Test wurde ohne weitere Parameterauswahl umgesetzt:

- Regel: VIX bei 13:35, Spike `>=35`, Rueckgang vom Peak `>=15`, Entry-VIX `>=25`.
- Struktur: SPXW Put-Credit, Ziel-Delta `-0.10`, 100 Punkte breit, Ziel 100DTE.
- Exit: circa 60DTE.
- Ausfuehrung: konservative Leg-Bid/Ask-Preise; `entry_credit_cons > 0` ist jetzt ein explizites Order-Gate und kein nachtraeglicher Summary-Filter.
- Kosten: 1,65 USD je Leg und Seite, insgesamt 6,60 USD je vollstaendig geschlossenem Spread.
- Zeitraum der lokal vorhandenen VIX-Rohdaten: 2021-01-29 bis 2026-08-07.

Ergebnis:

| Signal | Entry | Struktur | Credit | Exit | Netto-P/L 1x |
| --- | --- | --- | ---: | --- | ---: |
| Peak 2025-04-08 bei 49,12; Drop 18,20 | 2025-04-14 bei VIX 30,92 | 4250/4150 Put-Credit, Exp. 2025-08-15 | 5,60 | 2025-06-16 | +463,40 USD |

Die vollstaendige Historie erzeugt nur ein Signal und einen Trade. Dessen maximaler definierter Verlust betraegt inklusive Kosten etwa 9.446,60 USD. Der Gewinn entspricht damit rund 4,9% des maximalen Risikos. Die alte Credit-Budget-Skalierung ist fuer SPX nicht praktisch umsetzbar, weil sie Bruchteile eines Kontrakts verwendet.

Urteil: Der einzelne Trade bestaetigt die wirtschaftliche Plausibilitaet des Ereignisses, aber nicht die Strategie. Reifegrad und Arbeitszustand bleiben `idea/parked`. Keine IBKR-Integration und keine weitere Schwellenoptimierung auf derselben Episode. Wiederaufnahme erst mit neuen unabhaengigen Panikereignissen oder als vorab definierte, fachlich neue Hypothese.

Neue Artefakte:

- `D:\PycharmProjects\PythonProject\research\test_100dte_vix_reversal_put_credit_preregistered.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\signals.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\trades.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\summary.csv`

## 12. Isolierter Spike-Schwellen-Test vom 2026-08-09

Auf Nutzerwunsch wurde ausschliesslich die Spike-Schwelle von 35 in Zweierschritten bis 15 reduziert. Drop `>=15` und Entry-VIX `>=25` blieben unveraendert. Alle elf Schwellen erzeugen exakt dasselbe einzelne Signal am 2025-04-14.

Das Ergebnis ist logisch zwingend: Drop und Mindest-Entry-VIX setzen bereits einen impliziten Mindest-Peak von `15 + 25 = 40` voraus. Eine nominelle Spike-Schwelle unter 40 kann daher allein keine zusaetzlichen Einstiege zulassen. Mehr Signale erfordern eine Aenderung der Reversal-Distanz oder des Mindest-Entry-VIX; dies waere eine neue Testdimension.

Artefakt: `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\spike_threshold_sweep.csv`.

## 13. Zweidimensionaler Spike-/Drop-Signal-Screen vom 2026-08-09

Spike wurde von 35 bis 25 und Drop von 15 bis 5 jeweils in Zweierschritten variiert. Entry-VIX blieb `>=25`; Options-P/L wurde fuer die Auswahl nicht verwendet.

Vor der Auswertung wurde ein Logikfehler korrigiert: Ein VIX-Peak blieb zuvor unbegrenzt aktiv und konnte Monate spaeter ein falsches Reversal erzeugen. Ein Reversal muss nun innerhalb der zehn nachfolgenden Handelssitzungen eintreten.

Signalzahlen nach der Korrektur:

| Spike / Drop | 15 | 13 | 11 | 9 | 7 | 5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 35 | 1 | 1 | 1 | 1 | 3 | 3 |
| 33 | 1 | 1 | 1 | 3 | 6 | 9 |
| 31 | 1 | 1 | 1 | 3 | 6 | 9 |
| 29 | 1 | 1 | 1 | 3 | 6 | 10 |
| 27 | 1 | 1 | 1 | 3 | 6 | 10 |
| 25 | 1 | 1 | 1 | 3 | 6 | 10 |

Der vorsichtigste Kandidat mit nahezu zehn Ereignissen ist `Spike>=33 / Drop>=5 / Entry-VIX>=25`: neun Signale. Eine weitere Senkung des Spikes auf 31 bringt keinen zusaetzlichen Trade; erst Spike 29 fuegt bei Drop 5 genau ein Ereignis hinzu. Deshalb wird 33/5 fuer den naechsten Options-Proxy vorgemerkt, ohne anhand von P/L gewaehlt worden zu sein.

Die neun Signale sind zeitlich stark geclustert: zwei 2021, fuenf 2022, eines 2024 und eines 2025. Sie sind damit keine neun unabhaengigen Marktregime. Der Kandidat bleibt Research und wird erst nach Options-Proxy und anschliessender punktueller Exact-Quote-Pruefung bewertet.

Artefakt: `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\spike_drop_signal_sweep.csv`.

## 14. Frequenzkandidat mit 6-12 Signalen pro Jahr

Da der seltene Panik-Reversal-Pfad nicht die gewuenschte Handelsfrequenz erreicht, wurde ein rein frequenzbasierter Screen ueber Spike, Drop und Mindest-Entry-VIX ausgefuehrt. Options-P/L war kein Auswahlkriterium. Das Reversal-Fenster blieb bei zehn Handelssitzungen.

Der erste Kandidat, der in jedem vollstaendigen Jahr 2021-2025 das Zielband erreicht, ist:

- Spike `>=19`
- Drop vom Peak `>=1`
- Entry-VIX `>=15`
- Reversal innerhalb von zehn Handelssitzungen
- 25 Kalendertage Cooldown

| Jahr | Signale |
| ---: | ---: |
| 2021 | 10 |
| 2022 | 12 |
| 2023 | 7 |
| 2024 | 6 |
| 2025 | 7 |
| 2026 bis 07.08. | 6 |

Insgesamt entstehen 48 Signale. Diese Regel ist fachlich keine seltene Panik-Reversal-Regel mehr, sondern ein VIX-Pullback nach erhoehter Volatilitaet. Sie muss deshalb als eigene Strategievariante bewertet werden. Noch offen sind Options-Proxy, handelbare Positive-Credit-Quotes, Ueberlappung gleichzeitig offener 100DTE-Spreads und realistisches Portfolio-Risiko.

Artefakte:

- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\annual_frequency_target_sweep.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\thetadata_100dte_vix_reversal_put_credit_preregistered_v1\frequency_candidate_spike19_drop1_vix15_cd25.csv`

## 15. Exit-Proxy fuer den Frequenzkandidaten

Die 48 Signale wurden mit einem kalibrierten SPX-Optionsproxy bewertet. Modell: lokale SPX-/VIX-Pfade, 100DTE, Delta circa -0,10, 100 Punkte Breite, VIX plus 5,5 IV-Punkte, Put-Skew, VIX-abhaengiger Liquiditaetshaircut, Positive-Credit-Gate und 6,60 USD Roundtrip-Gebuehren. Der Proxy reproduziert den bekannten nicht handelbaren 2025-04-09-Entry als `blocked_nonpositive_credit`.

| Exit | Closed | Netto-P/L | Win Rate | Max realisierter DD | Worst Trade | Ø Haltedauer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hold bis Verfall | 43 | +3.654 USD | 95,3% | -12.507 USD | -9.615 USD | 114,6 Tage |
| Exit 60DTE | 44 | -3.080 USD | 70,5% | -8.416 USD | -3.902 USD | 54,9 Tage |
| TP 10% | 45 | +1.444 USD | 95,6% | -2 USD | -2 USD | 15,5 Tage |
| TP 15% | 45 | +2.315 USD | 100,0% | 0 USD | +0 USD | 15,8 Tage |
| TP 20% | 45 | +3.185 USD | 100,0% | 0 USD | +2 USD | 18,5 Tage |
| TP 25% | 45 | +4.056 USD | 100,0% | 0 USD | +5 USD | 20,2 Tage |
| TP 40% | 44 | -3.081 USD | 97,7% | -9.615 USD | -9.615 USD | 25,9 Tage |
| TP 50% | 44 | -1.389 USD | 97,7% | -9.615 USD | -9.615 USD | 30,1 Tage |
| TP 80% | 43 | +428 USD | 95,3% | -12.507 USD | -9.615 USD | 56,9 Tage |

Hold bis Verfall ist trotz positivem Gesamt-P/L nicht sinnvoll: 2022 allein verliert rund 7.582 USD und zwei nahezu volle Spread-Verluste dominieren die Kurve. Der 60DTE-Roll realisiert Drawdowns zu frueh und ist negativ.

Der Proxy bevorzugt 25% Take-Profit. Das Ergebnis ist jedoch nicht als 100%-Trefferstrategie zu interpretieren: Mehrere spaetere Gewinner durchlaufen vorher modellierte Zwischenverluste bis etwa 4.643 USD. Ausserdem kippt das Gesamtergebnis zwischen TP25 und TP40, weil ein einzelner Januar-2022-Trade das 40%-Ziel vor dem Selloff nicht erreicht. Diese Schwellenabhaengigkeit verlangt echte historische Exit-Quotes.

Ein Bulk-Abruf von vier ThetaData-Ketten (zwei 2022-Stressfaelle sowie 2024/2025-Anker) lieferte innerhalb des begrenzten Laufs keine Antwort und wurde beendet. `TP25` ist daher nur der Kandidat fuer einen fokussierten Exact-Quote-Abruf, nicht integrationsreif.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\backtest_vix_pullback_100dte_exit_proxy.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_exit_proxy_v1\exit_policy_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_exit_proxy_v1\exit_policy_trades.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_exit_proxy_v1\modeled_daily_paths.csv`

## 16. ThetaData-Exact-Quote-Test 2023-2026 mit TP25

Der fokussierte Abruf verwendet echte historische SPX-NBBO-Quotes statt modellierter
Optionspreise. Pro Signal wird der tatsaechlich gelistete Put mit Delta nahe -0,10
gesucht, der 100-Punkte-Long-Put ergaenzt und die Historie beider Legs in
resumierbaren 28-Tage-Bloecken geladen. Entry-Credit ist konservativ
`Short Bid - Long Ask`; der Exit wird mit `Short Ask - Long Bid` bewertet. Das
25%-Take-Profit gilt erst nach zwei aufeinanderfolgenden, hoechstens 90 Minuten
auseinanderliegenden Quote-Beobachtungen. Roundtrip-Kosten: 6,60 USD.

Bei der Verfallsauswahl wurde ein Fehler korrigiert: Feiertagsbedingt auf Donnerstag
vorgezogene monatliche Verfaelle werden akzeptiert, normale woechentliche
Donnerstag-Verfaelle jedoch nicht.

Ergebnis fuer die 26 Signale 2023 bis 07.08.2026:

| Kennzahl | Ergebnis |
| --- | ---: |
| Vollstaendig geschlossene Trades | 22 |
| Nicht handelbarer Entry | 1 |
| Nicht vollstaendig auswertbar | 3 |
| Realisierte Netto-P/L | +2.922,30 USD |
| Endwert bei 25.000 USD Start | 27.922,30 USD |
| Realisierte Gewinner | 22 von 22 |
| Durchschnittliche Haltedauer | 14,5 Tage |
| Taeglicher Portfolio-MTM-Max-Drawdown | -1.873,20 USD |
| Niedrigster Portfolio-MTM-Kontowert | 24.217,70 USD |
| Schlechtester intraday beobachteter Einzeltrade-MAE | ca. -9.463 USD |

Jahres-P/L nach Exit: 2023 `+1.003,80`, 2024 `+597,00`, 2025 `+672,00`,
2026 bis 07.08. `+649,50 USD`.

Nicht in der Equity enthalten sind die Signale 2024-12-20 und 2025-03-07 wegen
fehlender Entry-Greeks sowie 2026-06-11, weil kein exakter 100-Punkte-Long-Strike
verfuegbar war. Der 2025-04-09-Trade wurde regelgerecht blockiert, weil der
konservative Entry-Credit negativ war.

Urteil: TP25 bleibt ein klarer Research-Kandidat, ist aber noch nicht
produktionsreif. Die 100%-Quote gilt nur fuer 22 vollstaendig auswertbare Trades;
drei fehlende Faelle duerfen nicht stillschweigend als Nicht-Trades interpretiert
werden. Die Realized-P/L-Treppe verdeckt ausserdem erhebliche zwischenzeitliche
Risiken. Vor Integration sind die drei Datenluecken, Portfolio-Risikolimit und
eine unabhaengige Out-of-Sample-Fortschreibung festzulegen.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\backtest_vix_pullback_100dte_tp25_thetadata.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_tp25_thetadata_2023_2026_v1\trades_tp25_thetadata.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_tp25_thetadata_2023_2026_v1\daily_mark_to_market_equity.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_tp25_thetadata_2023_2026_v1\equity_curve_tp25_thetadata_2023_2026.png`

## 17. Exact-Quote-Vergleich der Spreadbreite

Mit identischem Signal, Short-Delta, Verfall und TP25 wurden exakte Breiten von
15, 20, 25, 50 und 100 SPX-Punkten getestet. Die urspruengliche Auswertung war
methodisch fehlerhaft: Zuerst wurde der Short Put mit dem Delta am naechsten zu
-0,10 gewaehlt und erst danach geprueft, ob genau fuer diesen Strike der passende
Long Put existiert. Damit wurden schmale Spreads faelschlich als nicht verfuegbar
eingestuft, obwohl ein benachbarter Short Strike mit praktisch gleichem Delta ein
vollstaendiges Paar ergeben haette. Die korrigierte Auswahl bildet zuerst alle
tatsaechlich gelisteten Short/Long-Paare der gewuenschten Breite und waehlt danach
aus diesen Paaren das Short-Delta am naechsten zu -0,10. Die alten Ergebnisse fuer
15, 20 und 25 Punkte sind deshalb verworfen.

| Breite | Closed | Unavailable | Open | Netto-P/L | MTM Max DD | Durchschnittlicher Maximalverlust | Durchschnittliche Haltedauer |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 15 | 16 | 6 | 2 | +91,90 USD | -410,70 USD | 1.453,92 USD | 61,9 Tage |
| 20 | 16 | 7 | 1 | +216,90 USD | -463,20 USD | 1.922,68 USD | 41,3 Tage |
| 25 | 22 | 1 | 1 | +444,80 USD | -1.166,60 USD | 2.396,03 USD | 37,2 Tage |
| 50 | 22 | 2 | 1 | +1.289,80 USD | -1.713,20 USD | 4.742,39 USD | 25,0 Tage |
| 100 | 22 | 3 | 0 | +2.897,30 USD | -1.863,20 USD | 9.450,12 USD | 15,5 Tage |

Es existiert nun ein gemeinsamer Schnitt von 14 abgeschlossenen Trades ueber alle
fuenf Breiten. Auf genau diesen identischen Trades steigen Netto-P/L und mittlere
Haltedauer wie folgt: 15 Punkte +80,10 USD / 61,6 Tage, 20 Punkte +192,60 USD /
39,8 Tage, 25 Punkte +310,10 USD / 28,4 Tage, 50 Punkte +862,60 USD / 18,9 Tage
und 100 Punkte +1.885,10 USD / 13,2 Tage. Die bessere Verfuegbarkeit schmaler
Spreads ist damit bestaetigt; der wirtschaftliche Abstand bleibt jedoch bestehen.

Der Grund ist nicht die Strike-Verfuegbarkeit, sondern die Struktur: Der nahe Long
Put behaelt einen grossen Teil des Werts des Short Puts. Dadurch sinkt der Credit,
ein prozentualer Take-Profit entspricht weniger absoluten Dollars, und das Ziel
wird langsamer erreicht. Fixe Roundtrip-Gebuehren von 6,60 USD belasten den kleinen
Gewinn zusaetzlich. Pro Kontrakt darf 100 Punkte deshalb nicht allein anhand der
absoluten P/L als besser bezeichnet werden: Sein definierter Kapitaleinsatz ist
etwa 6,5-mal so hoch wie bei 15 Punkten. Fuer einen fairen Portfoliovergleich sind
gleiches Risikobudget und Kapitalbindung je Trade zu verwenden.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\compare_vix_pullback_100dte_widths_thetadata.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width_sweep_thetadata_2023_2026_v1\width_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width_sweep_thetadata_2023_2026_v1\equity_curve_width_comparison_tp25_2023_2026.png`

## 18. Momentumfilter, erneuter TP- und Breitentest

Als fachlich begruendeter, nicht datumsbezogener Entry-Filter wurde vorab
festgelegt:

`SPX-Kurs um 13:35 ET / SPX-Schlusskurs vor 20 Handelssitzungen - 1 > 0`.

Der aktuelle Einstiegskurs stammt aus der 13:35-ET-Kerze; der spaetere
Tagesschluss wird nicht verwendet. Damit ist der Filter look-ahead-frei. Er laesst
12 der 26 Signale passieren. Elf davon besitzen eine vollstaendige historische
100-Punkte-Struktur. Die problematischen Einstiege 2026-02-20 und 2026-03-23
werden mit Momentum von -0,27% beziehungsweise -3,45% ausgeschlossen.

### TP-Sweep auf 100 Punkten

Alle elf auswertbaren Trades erreichen saemtliche TP-Stufen bis TP95. Der
taegliche Portfolio-MTM-Max-Drawdown bleibt bei allen TP-Stufen -906,60 USD,
weil er vor dem fruehesten Take-Profit entsteht. Einige Eckpunkte:

| TP | Netto-P/L | Durchschnittliche Haltedauer |
| ---: | ---: | ---: |
| 25% | +1.454,90 USD | 15,8 Tage |
| 40% | +2.371,40 USD | 26,6 Tage |
| 50% | +2.982,40 USD | 32,1 Tage |
| 60% | +3.593,40 USD | 37,9 Tage |
| 70% | +4.204,40 USD | 48,2 Tage |
| 80% | +4.815,40 USD | 62,2 Tage |
| 95% | +5.731,90 USD | 99,5 Tage |

TP95 ist das retrospektive Maximum unter den geschlossenen Varianten, wird aber
nicht ausgewaehlt: Es ist fast ein Halten bis Verfall und stuetzt sich nur auf elf
Trades. TP60 ist der vorab begruendete Kniepunkt. Bis TP60 steigt die P/L bei
moderater zusaetzlicher Haltedauer; danach kostet derselbe inkrementelle Gewinn
zunehmend mehr Marktexposition.

### Breitentest mit Momentumfilter und TP60

| Breite | Verfuegbare Strukturen | Closed | Netto-P/L | MTM Max DD | Durchschnittliche Haltedauer |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 15 | 10 | 9 | +190,60 USD | -276,60 USD | 85,9 Tage |
| 20 | 10 | 10 | +378,00 USD | -316,60 USD | 75,9 Tage |
| 25 | 11 | 11 | +659,40 USD | -366,60 USD | 63,1 Tage |
| 50 | 11 | 11 | +1.673,40 USD | -586,60 USD | 45,4 Tage |
| 100 | 11 | 11 | +3.593,40 USD | -906,60 USD | 37,9 Tage |

Der Momentumfilter beseitigt den MTM-Drawdown-Cluster Anfang 2026. Die schmalen
SPX-Spreads sind nach der korrigierten gemeinsamen Strike-Auswahl deutlich besser
verfuegbar. Sie liefern pro Kontrakt trotzdem weniger Credit, brauchen bis TP60
laenger und tragen relativ hohe Gebuehren. Die absolute P/L bevorzugt weiterhin
100 Punkte, vergleicht aber sehr unterschiedliche definierte Risiken. Der naechste
faire Strukturtest muss daher alle Breiten auf dasselbe Risikobudget skalieren;
erst dann laesst sich beurteilen, ob 100 Punkte auch auf Portfolioebene ueberlegen
sind.

Urteil: Der Filter ist fachlich plausibel und das Ergebnis deutlich verbessert,
aber 11 vollstaendige Trades mit 100% Treffern reichen nicht fuer Produktion.
Momentumfilter und TP60 sind Research-Kandidaten fuer fortlaufendes Out-of-Sample-
Papertrading; eine weitere Schwellenoptimierung auf denselben 2023-2026-Daten wird
nicht vorgenommen.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\backtest_vix_pullback_momentum_tp_width_sweep.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\entry_momentum_filter.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\tp_sweep_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\width_sweep_tp60_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\width_sweep_tp60_equity.png`

## 19. Zwischenbreiten 50 bis 100 Punkte

Die exakten Breiten 60, 70, 80 und 90 Punkte wurden mit demselben Momentumfilter
und TP60 anhand echter ThetaData-NBBO-Pfade nachgeladen. Fuer einen fairen
Vergleich werden nur die zehn abgeschlossenen Trades verwendet, fuer die alle
sechs Breiten von 50 bis 100 Punkten vorhanden sind. Der Einstieg 2025-10-13 ist
bei 50 und 100 Punkten vorhanden, fuer die Zwischenbreiten fehlen jedoch exakte
Long-Wing-Quotes; er ist deshalb aus allen sechs Vergleichskurven entfernt.

| Breite | Netto-P/L | MTM Max DD | Durchschnittlicher Credit | Durchschnittliches Maximalrisiko | Haltedauer | P/L / Maximalrisiko |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | +1.512 USD | -586,60 USD | 2,63 | 4.740,30 USD | 43,2 Tage | 31,90% |
| 60 | +1.878 USD | -636,60 USD | 3,24 | 5.679,30 USD | 41,5 Tage | 33,07% |
| 70 | +2.220 USD | -706,60 USD | 3,81 | 6.622,30 USD | 41,0 Tage | 33,52% |
| 80 | +2.586 USD | -776,60 USD | 4,42 | 7.561,30 USD | 37,2 Tage | 34,20% |
| 90 | +2.880 USD | -846,60 USD | 4,91 | 8.512,30 USD | 36,8 Tage | 33,83% |
| 100 | +3.240 USD | -906,60 USD | 5,51 | 9.452,30 USD | 36,4 Tage | 34,28% |

Die absoluten Gewinne steigen nahezu linear mit der Breite. Gleichzeitig steigt
der beobachtete Drawdown deutlich langsamer als der definierte Maximalverlust,
weil die Long Wings in den historischen Ruecksetzern nur begrenzt wirksam werden.
Der 100-Punkte-Spread bleibt auf dieser kleinen Stichprobe bei absoluter P/L,
P/L-zu-Drawdown und P/L-zu-Maximalrisiko knapp vorne. Ein ausgepraegter optimaler
Zwischenwert existiert nicht.

80 Punkte bilden jedoch einen sinnvollen Risiko-Knick: Gegenueber 100 Punkten
werden durchschnittlich rund 1.891 USD definiertes Tail-Risiko eingespart. Die
P/L sinkt um 654 USD, die risikonormierte P/L bleibt mit 34,20% gegenueber 34,28%
praktisch identisch, und die Haltedauer steigt nur um 0,8 Tage. Daher ist 80 Punkte
der konservativere Strukturkandidat; 100 Punkte ist der hoehere absolute
Ertragskandidat. Zehn ausschliesslich profitable Trades reichen nicht aus, um die
kleine Differenz als stabilen Vorteil zu interpretieren.

Artefakte:

- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\width_sweep_tp60_common_50_100_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\width_sweep_tp60_common_50_100_equity.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_momentum_filter_tp_width_sweep_v1\width_sweep_tp60_equity_drawdown_50_100.png`

## 20. Short-Delta-Sweep bei 80 Punkten und TP60

Bei unveraendertem Signal, positivem 20-Sitzungen-Momentum, rund 100 DTE,
80 Punkten Breite und TP60 wurden Short-Put-Deltas von -0,10, -0,15, -0,20
und -0,25 mit echten ThetaData-NBBO-Pfaden getestet.

| Ziel-Delta | Closed | Gewinner | Netto-P/L | MTM Max DD | Durchschnittlicher Credit | Durchschnittliches Maximalrisiko | Haltedauer | P/L / Drawdown |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| -0,10 | 10 | 10 | +2.586,00 USD | -776,60 USD | 4,42 | 7.561,30 USD | 37,2 Tage | 3,33 |
| -0,15 | 12 | 12 | +4.978,80 USD | -1.106,60 USD | 7,03 | 7.300,80 USD | 41,5 Tage | 4,50 |
| -0,20 | 12 | 12 | +6.874,80 USD | -1.446,60 USD | 9,66 | 7.037,47 USD | 43,4 Tage | 4,75 |
| -0,25 | 12 | 11 | +1.240,80 USD | -7.886,60 USD | 12,33 | 6.769,97 USD | 52,6 Tage | 0,16 |

Die -0,10-Stichprobe ist nicht vollstaendig: Fuer die Einstiege 2024-12-20 und
2025-10-13 fehlen historische exakte Strukturen. Ein gemeinsamer Zehn-Trade-
Schnitt ueber alle vier Deltas entfernt damit ausgerechnet den Verlustfall der
-0,25-Variante und darf nicht fuer die Auswahl verwendet werden. Fuer den
belastbareren Vergleich besitzen -0,15, -0,20 und -0,25 dieselben zwoelf Trades.

-0,20 ist innerhalb dieses vorab festgelegten Sweeps der beste Kandidat. Gegenueber
-0,15 steigt die Netto-P/L um rund 38%, der MTM Max DD aber nur um rund 31%; alle
zwoelf Trades erreichen TP60. -0,25 ueberschreitet dagegen eine strukturelle
Risikogrenze. Der Einstieg 2024-12-20 (Short 5.650, Long 5.570, Credit 11,0)
erreicht zwar TP25, verfehlt aber die fuer TP60 geforderten zwei bestaetigenden
Quotes knapp: Das Ziel liegt bei 4,40 Debit, das beste aufeinanderfolgende Paar
am 2025-02-19 liegt bei 4,40 und 4,50. Nach der eingefrorenen Fill-Regel bleibt
der Trade deshalb offen und laeuft am 2025-04-17 mit dem vollen Spreadverlust von
-6.906,60 USD aus. Das Ergebnis ist damit stark von der Exit-Mechanik abhaengig;
es beweist nicht fuer sich allein, dass -0,25 grundsaetzlich unbrauchbar ist. Die
groessere Naehe zum Geld verlaengert ausserdem die mittlere Haltedauer.

Urteil: -0,20 ersetzt -0,10 noch nicht als Produktionsparameter, ist aber der
fachlich staerkste neue Research-Kandidat. Die kleine Stichprobe und die fehlenden
-0,10-Daten verhindern eine endgueltige Auswahl. Keine feinere Delta-Optimierung
auf denselben Daten; als naechster sinnvoller Schritt waere ein Managementtest
fuer -0,20 oder eine unabhaengige Out-of-Sample-Fortschreibung vorgesehen.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\compare_vix_pullback_100dte_short_deltas_thetadata.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_delta_sweep_thetadata_2023_2026_v1\all_available_tp60_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_delta_sweep_thetadata_2023_2026_v1\all_available_tp60_equity.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_delta_sweep_thetadata_2023_2026_v1\all_available_tp60_equity_drawdown_realized.png`

## 21. Fallschirm-Exits fuer Delta -0,20 und Stresstest mit Delta -0,25

Auf den identischen zwoelf Trades wurden bei 80 Punkten Breite und TP60 drei
fachlich vorab begruendete Schutzregeln einzeln und kombiniert getestet:

1. Risikostopp bei 20% des definierten Maximalrisikos.
2. Schliessen der noch offenen Position bei 60 Rest-DTE.
3. Thesis-Stopp, wenn der VIX-Tages-Schlusskurs das zum Signal gehoerende
   Spike-Hoch wieder erreicht oder ueberschreitet.

Der Risikostopp verwendet keine einzelnen Roh-Ticks. Beide Leg-Bids muessen
positiv sein, der relative Bid/Ask-Spread jedes Legs darf hoechstens 20% betragen,
und zwei qualifizierende Quotes muessen am selben Tag innerhalb von 90 Minuten
vorliegen. Gefuellt wird konservativ zum zweiten beobachteten Combo-Debit, nicht
zum theoretischen Stoppreis. Der VIX-Stopp ist kausal: Das Tagesschlusssignal kann
erst mit der ersten qualifizierten Optionsquote der folgenden Handelssitzung
ausgefuehrt werden.

### Delta -0,20: primaerer Research-Kandidat

| Exit-Regel | Gewinner | Netto-P/L | Realisierter Max DD | Taeglicher MTM Max DD | Schlechtester Trade | Schlechteste verlaessliche Trade-MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nur TP60 | 12/12 | +6.874,80 USD | 0,00 USD | -1.446,60 USD | +491,40 USD | -2.606,60 USD |
| 20%-Risikostopp | 11/12 | +4.818,80 USD | -1.396,60 USD | -1.693,20 USD | -1.396,60 USD | -1.446,60 USD |
| Zeitstopp 60 DTE | 12/12 | +6.570,80 USD | 0,00 USD | -1.446,60 USD | +373,40 USD | -2.606,60 USD |
| VIX-Reclaim | 6/12 | +1.856,80 USD | -833,20 USD | -983,20 USD | -686,60 USD | -686,60 USD |
| Alle drei | 6/12 | +1.838,80 USD | -833,20 USD | -983,20 USD | -686,60 USD | -686,60 USD |

Der enge Risikostopp reduziert die intraday beobachtete, qualitaetsgefilterte
Einzeltrade-MAE, verbessert aber nicht die Portfoliokurve. Er schliesst Event 23
am 2023-03-10 mit -1.396,60 USD; der Trade haette sich anschliessend erholt und
TP60 mit +659,40 USD erreicht. Durch den festgeschriebenen Verlust und einen
spaeter ueberlappenden Trade verschlechtert sich der taegliche Portfolio-DD sogar
von -1.446,60 auf -1.693,20 USD. Dies ist kein Widerspruch: Einzeltrade-Risikocap
und Portfolio-Drawdown messen unterschiedliche Dinge.

Der 60-DTE-Zeitstopp ist mild. Er betrifft nur zwei Trades, behaelt alle Gewinner
und kostet 304 USD P/L, ohne den bereits frueher entstandenen MTM-DD zu veraendern.
Der VIX-Reclaim senkt den DD am staerksten, beendet aber sechs Trades und vernichtet
rund 73% der Baseline-P/L. Er ist fuer diese Strategie zu empfindlich.

### Delta -0,25: diagnostischer Stresstest

| Exit-Regel | Gewinner | Netto-P/L | Realisierter Max DD | Taeglicher MTM Max DD | Schlechtester Trade |
| --- | ---: | ---: | ---: | ---: | ---: |
| Nur TP60 | 11/12 | +1.240,80 USD | -6.906,60 USD | -7.886,60 USD | -6.906,60 USD |
| 20%-Risikostopp | 10/12 | +3.858,80 USD | -1.936,60 USD | -2.916,60 USD | -1.936,60 USD |
| Zeitstopp 60 DTE | 12/12 | +8.158,80 USD | 0,00 USD | -1.676,60 USD | +283,40 USD |
| VIX-Reclaim | 5/12 | +728,80 USD | -1.263,20 USD | -1.966,60 USD | -986,60 USD |
| Alle drei | 6/12 | +2.288,80 USD | -973,20 USD | -1.123,20 USD | -766,60 USD |

Der Zeitstopp verhindert hier den bekannten vollen Spreadverlust: Der
2024-12-20-Trade wird bei 60 Rest-DTE mit 4,50 Debit und +643,40 USD geschlossen,
statt am Verfall -6.906,60 USD zu verlieren. Eine grobe Robustheitskontrolle von
50, 55, 60, 65 und 70 Rest-DTE reduziert den taeglichen MTM Max DD in allen
Varianten auf -1.676,60 USD. Zwischen 50 und 60 DTE bleiben alle zwoelf Trades
profitabel; bei 65 und 70 DTE entstehen kleine Verluste. Das Resultat ist damit
nicht auf exakt einen einzigen DTE-Wert beschraenkt.

Trotzdem darf Delta -0,25 nicht nachtraeglich zum Gewinner erklaert werden: Der
Zeitstopp wurde nach Sichtung seines Verlustfalls untersucht, und die Stichprobe
enthaelt nur zwoelf Trades. Der starke P/L-Sprung stammt wesentlich aus der
Rettung genau dieses einen TP-Near-Miss. Er ist ein plausibler Managementkandidat,
aber noch kein unabhaengiger Beleg fuer Alpha.

Urteil: Fuer Delta -0,20 wird der 60-DTE-Zeitstopp als sinnvollster Research-
Fallschirm vorgemerkt. Der VIX-Reclaim wird verworfen. Der 20%-Risikostopp kann als
explizite Sicherheitsgrenze sinnvoll sein, ist aber in dieser Stichprobe keine
Performanceverbesserung und muss getrennt von der Alpha-Regel bewertet werden.
Keine Bot- oder Produktionsaenderung erfolgt aufgrund dieses In-Sample-Tests.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\test_vix_pullback_100dte_parachute_exits.py`
- `D:\PycharmProjects\PythonProject\tests\test_vix_pullback_100dte_parachute_exits.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width80_parachute_exits_v1\parachute_exit_summary_all_deltas.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width80_parachute_exits_v1\delta_20\time_stop_dte_sensitivity.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width80_parachute_exits_v1\delta_25\time_stop_dte_sensitivity.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width80_parachute_exits_v1\delta_20\parachute_exit_comparison.png`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_100dte_width80_parachute_exits_v1\delta_25\parachute_exit_comparison.png`

## 22. XSP-Skalierung mit zehn Punkten Breite

Um das definierte Risiko strukturell statt nur durch einen Stopauftrag zu
begrenzen, wurde derselbe VIX-Pullback auf XSP uebertragen. XSP bildet ein Zehntel
des SPX ab. Getestet wurden Ziel-Delta -0,20, exakt zehn Punkte Breite, TP60 und
der 60-DTE-Zeitstopp. Entry und Exit verwenden echte historische ThetaData-NBBO-
Quotes; Gebuehren von 6,60 USD je Roundtrip sind enthalten.

Die Stichprobe besteht aus denselben zwoelf Momentum-gefilterten Signaltagen wie
der SPX-Delta--0,20-Test. Elf XSP-Strukturen sind grundsaetzlich handelbar. Am
2024-10-04 liefert ThetaData fuer die benoetigten XSP-Strikes um 13:35 ET keine
Entry-Greeks. Dieser Tag wird nicht modelliert oder ersetzt.

### Naechstes tatsaechlich handelbares Delta

Bei zehn der elf Strukturen liegt das tatsaechliche Short-Delta zwischen -0,2081
und -0,1987. Beim wichtigen Einstieg 2024-12-20 ist am Zielbereich jedoch nur
Delta -0,17 handelbar; die hoeheren gelisteten Strikes besitzen um 13:35 ET keine
Greeks/Quotes. Deshalb wird das Ergebnis zusaetzlich mit einer strengen maximalen
Delta-Abweichung von 0,015 ausgewiesen.

| Sample und Exit | Closed | Gewinner | Netto-P/L | Taeglicher MTM Max DD | Schlechtester Trade | Mittleres Maximalrisiko | Hoechstes Maximalrisiko | Haltedauer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Naechstes handelbares Delta, nur TP60 | 11 | 10 | -320,20 USD | -1.040,60 USD | -937,60 USD | 893,48 USD | 934,30 USD | 57,3 Tage |
| Naechstes handelbares Delta, TP60 + 60 DTE | 11 | 11 | +577,60 USD | -320,60 USD | +24,40 USD | 893,48 USD | 934,30 USD | 49,3 Tage |
| Streng Delta +/-0,015, nur TP60 | 10 | 10 | +617,40 USD | -288,60 USD | +52,20 USD | 889,40 USD | 905,30 USD | 51,2 Tage |
| Streng Delta +/-0,015, TP60 + 60 DTE | 10 | 10 | +547,20 USD | -288,60 USD | +24,40 USD | 889,40 USD | 905,30 USD | 48,3 Tage |

Der 60-DTE-Zeitstopp verhindert im erweiterten Sample erneut den vollen Verlust
des 2024-12-20-Trades: Die Delta--0,17-Ersatzstruktur wird bei 60 Rest-DTE mit
+30,40 USD geschlossen, statt am Verfall -937,60 USD zu verlieren. Dieser Befund
ist wirtschaftlich relevant, aber kein exakter Delta--0,20-Stresstest. Im strengen
Delta-Sample fehlt genau dieses Ereignis; dort senkt der Zeitstopp die P/L um
70,20 USD und veraendert den DD nicht.

Das Kontorisiko passt wesentlich besser: Ein einzelner Trade riskiert maximal
934,30 USD beziehungsweise 3,74% eines 25.000-USD-Kontos. Historisch waren
hoechstens zwei Positionen gleichzeitig offen; deren summiertes definiertes
Risiko lag maximal bei 1.794,60 USD beziehungsweise 7,18% des Kontos. Damit bleibt
das Portfolio auch ohne garantierten Stop-Fill unter der vorgesehenen
Zehn-Prozent-Grenze.

Die kleinere Struktur erhoeht den relativen Kostenanteil. Beim verwalteten
Elf-Trade-Sample entsprechen 72,60 USD Roundtrip-Gebuehren 11,2% der P/L vor
Gebuehren. Eine zusaetzliche konservative Roundtrip-Slippage von 0,10 XSP-Punkten
je Trade reduziert die Netto-P/L von 577,60 auf 467,60 USD, laesst sie aber positiv.

Urteil: XSP mit zehn Punkten loest das zentrale Positionsgroessenproblem und ist
der bislang plausibelste handelbare Research-Kandidat dieser Familie. Das Ergebnis
ist wegen nur zehn bis elf Trades, einer Datenluecke und des abweichenden Deltas am
wichtigsten Stressereignis noch keine Produktionsfreigabe. Als naechster Schritt
ist Papertrading mit strenger Quote-/Delta-Pruefung sinnvoll; die bisherige SPX-
Produktion wird nicht automatisch ersetzt.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\backtest_xsp_vix_pullback_100dte_10wide_thetadata.py`
- `D:\PycharmProjects\PythonProject\tests\test_xsp_vix_pullback_100dte_10wide.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_slippage_sensitivity.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_portfolio_risk_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_equity_drawdown.png`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_equity_drawdown_strict_delta.png`

## 23. Erweiterung des XSP-Tests auf 2021 und 2022

Der XSP-Test wurde ohne Aenderung der Entry-Regeln auf 2021 und 2022 erweitert.
Der kausale 20-Sitzungen-Momentumfilter laesst sieben der zehn Signale aus 2021
und sechs der zwoelf Signale aus 2022 passieren. Zusammen mit 2023-2026 entstehen
25 gefilterte Signale. Eine Struktur am 2022-04-08 wird wegen eines negativen
konservativen Entry-Credits blockiert; 24 Strukturen bleiben auswertbar.

Bei der Erweiterung wurde ein Darstellungsfehler gefunden und korrigiert: Die
generische Portfolio-MTM-Funktion begann zuvor fest am 2023-01-01. Realisierte
P/L aus 2021/2022 wurde zwar addiert, ihre damaligen MTM-Schwankungen fehlten aber
im Drawdown. Die Kurve beginnt nun immer mit dem fruehesten tatsaechlichen Entry.

XSP besitzt haeufig einzelne unhandelbare Leg-Quotes. Deshalb werden zwei
Drawdowns ausgewiesen:

- `Roh-DD`: konservativer Short-Ask minus Long-Bid ohne weiteren Filter.
- `Q-DD`: beide Bids positiv und relativer Bid/Ask-Spread jedes Legs hoechstens
  20%; fehlende qualifizierte Tagesmarks werden mit dem letzten qualifizierten
  Mark fortgeschrieben.

Der Roh-DD bleibt als obere Stressmarke erhalten. Beispielsweise bewertet ein
unhandelbarer Quote am 2021-11-26 einen Spread fast zum vollen Wert, obwohl die
qualitaetsgefilterte Einzeltrade-MAE nur -328,60 USD erreicht.

### Gesamtresultat 2021-2026

| Sample und Management | Closed | Gewinner | Netto-P/L | Roh-DD | Q-DD | Schlechtester Trade | Max. definiertes Einzelrisiko |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Naechstes handelbares Delta, nur TP60 | 24 | 22 | -388,00 USD | -1.575,60 USD | -1.353,80 USD | -937,60 USD | 934,30 USD |
| Naechstes handelbares Delta, TP60 + 60 DTE | 24 | 19 | +611,60 USD | -918,60 USD | -608,20 USD | -154,60 USD | 934,30 USD |
| Naechstes handelbares Delta, zusaetzlich 10%-Risikocap | 23 | 19 | +687,20 USD | -918,60 USD | -532,60 USD | -154,60 USD | 934,30 USD |
| Streng Delta +/-0,015, TP60 + 60 DTE | 22 | 17 | +533,20 USD | -918,60 USD | -608,20 USD | -154,60 USD | 905,30 USD |
| Streng Delta +/-0,015, zusaetzlich 10%-Risikocap | 21 | 17 | +608,80 USD | -918,60 USD | -532,60 USD | -154,60 USD | 905,30 USD |

Die 2.500-USD-Portfoliogrenze ist kausal und stammt aus der bereits festgelegten
Zehn-Prozent-Risikoregel. Ohne sie waeren am 2022-01-11 drei Positionen mit
2.656,90 USD definiertem Risiko gleichzeitig offen. Mit der Grenze wird der
dritte Trade abgelehnt; maximal bleiben zwei Positionen und 1.817,60 USD
beziehungsweise 7,27% Kontorisiko. Der abgelehnte Trade haette -75,60 USD
verloren. Das verbessert die historische P/L, ist aber keine nachtraeglich auf
sein Ergebnis zugeschnittene Regel.

### Teilperioden mit Zeitstopp und Portfoliocap

| Entry-Jahr | Trades | Gewinner | Netto-P/L |
| ---: | ---: | ---: | ---: |
| 2021 | 7 | 5 | +140,40 USD |
| 2022 | 4 | 2 | -78,80 USD |
| 2023 | 3 | 3 | +160,60 USD |
| 2024 | 4 | 4 | +185,80 USD |
| 2025 | 4 | 4 | +239,80 USD |
| 2026 | 1 | 1 | +39,40 USD |

2022 bleibt trotz Momentumfilter und Risikocap negativ. Der 60-DTE-Ausstieg
verhindert den vollen Verlust des Januar-Trades, aber die Einstiege vom 2022-06-07
und 2022-08-03 verlieren -78,60 beziehungsweise -154,60 USD. Die Erweiterung
zeigt damit realistisch, dass die Strategie nicht nur aus Gewinnern besteht.

Die Kostenrobustheit bleibt positiv: Bei naechstem handelbarem Delta, Zeitstopp
und Risikocap reduziert eine zusaetzliche Roundtrip-Slippage von 0,10 XSP-Punkten
je Trade die P/L von 687,20 auf 457,20 USD. Die kleine absolute P/L und der
qualitaetsgefilterte DD von -532,60 USD ergeben aber nur einen moderaten
Ertrag/Risiko-Faktor von 1,29.

Urteil: Die Erweiterung bestaetigt, dass XSP das Tail- und Kontogroessenproblem
loest. Sie schwaecht zugleich das zuvor zu positive Performancebild. Der Kandidat
ist als kleines, wenig korreliertes Papertrading-Standbein plausibel, aber kein
starker Alpha-Traeger und noch nicht produktionsreif. Vorgemerktes Management:
zehn Punkte Breite, Ziel-Delta -0,20 mit strenger Quotepruefung, TP60, Ausstieg bei
60 Rest-DTE und 2.500 USD maximales gleichzeitig definiertes Portfoliorisiko.

Zusaetzliche Artefakte:

- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_yearly_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_v1\xsp_10wide_portfolio_defined_risk.csv`

## 24. Paper-Bot-Integration vom 2026-08-11

Auf Nutzerentscheidung wird der XSP-Kandidat ab 2024 als stabil genug fuer eine
kleine, ueberwachte Paper-Fortschreibung angesehen. Das ist keine
Produktionsfreigabe; die negative 2022-Teilperiode bleibt ausdruecklich Teil der
Stressevidenz.

Integriert wurden:

- separat schaltbarer Runner-Pfad `--enable-xsp-vix-pullback`;
- identische kausale Signalmaschine wie im Research (48/48 historische
  Signaldaten stimmen ueberein);
- Standard-Monatsverfall ab 100 DTE, Delta `-0,20 +/- 0,015`, zehn Punkte Breite
  und Positive-Credit-Gate;
- sofortige inverse GTC-Combo nach Entry-Fill fuer TP60;
- 60-DTE-Zeitexit erst nach bestaetigter TP-Stornierung und Quote-Qualitaetsgate;
- persistenter Restart-/Duplikatschutz, Journal-Manifest, DecisionRecord,
  Zwei-Leg-TradeIntent und Execution-Ingest;
- harter DU-Paperkonto-Guard und gemeinsamer 2.500-USD-Optionsrisikodeckel.

Reifegrad: `paper_monitoring`. Parameter bleiben eingefroren; naechster Review
erfolgt anhand realer Paper-Fills und der Abweichung zur historischen
ThetaData-Auswertung.

## 25. Kritische Zeitachsenkorrektur vom 2026-08-11

Die lokalen CBOE-Indexdateien fuer SPX, VIX, VXN und RVX verwenden Central Time.
Der bisherige Research-Signalsatz las VIX bei `13:35` aus dieser Datei, also um
`14:35 ET`. Die zugehoerigen historischen Optionsquotes wurden dagegen korrekt
um `13:35 ET` geladen. Damit enthielt das Signal eine Stunde Zukunftsinformation.

Die Produktions- und Research-Zeitachse wurde auf den lokalen Rohdatenbalken
`12:35 CT = 13:35 ET` korrigiert. Die alte 48-Signal-Liste und saemtliche daraus
abgeleiteten Performanceaussagen in den Abschnitten 22 bis 24 sind fuer die
Strategiebewertung invalidiert. Nur die technische IBKR-/Journal-Integration
bleibt als getestet bestehen.

Ein kausaler Neuaufbau ergibt 48 Rohsignale und nach positivem
20-Sitzungen-Momentum 26 Einstiege; nur acht davon ueberlappen mit dem alten
momentumgefilterten Sample. Ein erster konservativer Optionsproxy ist fuer XSP
2022-2026 insgesamt negativ und reicht nicht fuer eine erneute Freigabe. Der
Pfad bleibt daher hoechstens experimentelles Paper-Monitoring; erforderlich ist
eine neue Exact-Quote-Auswertung der korrigierten Signale.

## 26. Kausale Exact-Quote-Revalidierung v1.0.1 vom 2026-08-17

Die in Abschnitt 25 geforderte Neuberechnung ist abgeschlossen. Der Signalsatz
wird direkt mit derselben versionierten Signalmaschine wie der Live-Bot erzeugt:
lokaler CBOE-Balken `12:35 CT`, Entscheidung und XSP-Quote `13:35 ET`. Die alten
Ergebnisdateien wurden nicht ueberschrieben; die kausale Auswertung besitzt einen
eigenen Ergebnisordner.

Eingefrorene Regeln:

- VIX-Peak mindestens 19, Rueckgang mindestens einen Punkt innerhalb von zehn
  Sitzungen, Entry-VIX mindestens 15;
- 25 Kalendertage Cooldown und positives SPX-Momentum ueber 20 Sitzungen;
- XSP Standard-Monatsverfall mit mindestens 100 Kalendertagen Restlaufzeit;
- Short-Put Delta `-0,20 +/-0,015`, exakt zehn Punkte Breite;
- konservativer Entry `Short Bid - Long Ask`, TP60 und Zeitexit bei 60 Rest-DTE;
- 6,60 USD Roundtrip-Gebuehren und maximal 2.500 USD gleichzeitig definiertes
  Risiko.

Von 2021 bis zum letzten vollstaendigen Rohdatentag 2026-08-14 entstehen 48
Rohsignale und 26 momentumgefilterte Signale. 23 Strukturen sind handelbar, zwei
Tage besitzen keine Entry-Greeks, und am 2022-03-22 ist der konservative Credit
nicht positiv. Von den 23 Strukturen sind 22 abgeschlossen und eine vom
2026-07-24 noch offen. Alle 23 handelbaren Strukturen liegen innerhalb der
strengen Delta-Toleranz; alle Entry-Legs bestehen zudem das 20%-Quote-Gate mit
tatsaechlichen relativen Spreads von nur rund 0,7 bis 3,9 Prozent.

### Ergebnis der eingefrorenen Variante

| Metrik | Ergebnis |
| --- | ---: |
| Abgeschlossene Trades | 22 |
| Gewinner | 19 (86,4%) |
| Realisierte Netto-P/L | +490,60 USD |
| Realisierter Max Drawdown | -468,20 USD |
| Qualitaetsgefilterter taeglicher MTM Max DD | -569,80 USD |
| Konservativer Roh-MTM Max DD | -919,60 USD |
| Schlechtester geschlossener Trade | -442,60 USD |
| Durchschnittlicher Entry-Credit | 1,18 Punkte |
| Durchschnittliches / hoechstes Maximalrisiko | 885,30 / 917,30 USD |
| Maximale gleichzeitige Positionen / definiertes Risiko | 2 / 1.799,60 USD |
| Mittlere Haltedauer | 50,0 Tage |
| Roundtrip-Gebuehren | 145,20 USD |

Teilperioden der abgeschlossenen Trades: 2021 `+146,00`, 2022 `-295,80`, 2023
`+161,00`, 2024 `+148,40`, 2025 `+239,20` und 2026 `+91,80` USD. Die Strategie
ist damit nicht in allen Regimen profitabel. Insbesondere der Zeitexit des
2022-08-25-Trades verliert 442,60 USD und zeigt, dass die hohe Trefferquote kein
Ersatz fuer definiertes Risiko ist. Der offene Trade vom 2026-07-24 lag am
2026-08-14 konservativ mit 20,40 USD im Plus.

Eine zusaetzliche Roundtrip-Slippage von 0,02 / 0,05 / 0,10 XSP-Punkten reduziert
die Netto-P/L auf 446,60 / 380,60 / 270,60 USD; der Erwartungswert bleibt in
dieser Sensitivitaet positiv. Der 2.500-USD-Portfoliodeckel blockiert historisch
keinen Trade, weil maximal zwei Positionen mit zusammen 1.799,60 USD Risiko
offen sind.

### Management-Robustheit ohne neue Parameterwahl

Auf denselben 23 Strukturen wurden die direkten Nachbarn TP50/60/70 und Zeitexit
bei 50/60/70 Rest-DTE kombiniert. Alle neun Zellen bleiben positiv; die Netto-P/L
liegt zwischen +239,80 und +991,50 USD. Das verringert die Sorge, dass nur exakt
TP60/60-DTE funktioniert. Die historisch beste Zelle TP70/50-DTE wird ausdruecklich
nicht uebernommen: Sie wurde auf denselben nur 22 abgeschlossenen Trades gefunden
und waere eine nachtraegliche Optimierung. Version 1.0.1 bleibt unveraendert.

### Technischer Abschluss und Urteil

Beim Paritaetscheck wurde ein Live-Fehler gefunden: Das konfigurierte
20%-Quote-Quality-Gate war beim Zeitexit aktiv, aber nicht vor dem Entry. Der
Entry blockiert nun ebenfalls, wenn eines der beiden Legs den Grenzwert verletzt
oder keinen endlichen Spread besitzt. Der Regressionstest sowie die Signal-,
Struktur-, Risikocap- und Exit-Tests laufen erfolgreich.

Urteil: Der kausale Exact-Quote-Test stellt einen kleinen positiven Erwartungswert
wieder her und rechtfertigt **Paper-Trading mit Orderausloesung im DU-Konto**.
Die Strategie ist wegen nur 22 abgeschlossenen Trades, negativer 2022-Teilperiode,
moderatem P/L-zu-MTM-DD-Verhaeltnis und noch fehlendem vollstaendigen Paper-
Lebenszyklus **nicht produktionsreif**. Parameter bleiben eingefroren. Der naechste
Nachweis sind echte Paper-Entries, GTC-TP beziehungsweise 60-DTE-Exit und deren
vollstaendige Journal-Reconciliation; keine weitere historische Filterwahl.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\backtest_xsp_vix_pullback_causal_v1_0_1.py`
- `D:\PycharmProjects\PythonProject\research\analyze_xsp_vix_pullback_causal_v1_0_1_robustness.py`
- `D:\PycharmProjects\PythonProject\tests\test_xsp_vix_pullback_causal_v1_0_1.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_causal_v1_0_1\xsp_10wide_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_causal_v1_0_1\xsp_10wide_tp_time_robustness.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_pullback_xsp_100dte_delta20_width10_thetadata_causal_v1_0_1\xsp_10wide_equity_drawdown_strict_delta.png`

## 27. Frequenzhypothese VIX ueber EMA20 vom 2026-08-17

Als eigenstaendige, noch nicht integrierte Variante wurde geprueft, ob auf die
VIX-Rueckgangsbestaetigung verzichtet werden kann. Das kausale Signal entsteht
beim ersten Ueberschreiten des 20-Sitzungen-EMA durch den VIX um 13:35 ET, sofern
VIX mindestens 15 betraegt. Ein durchgehend oberhalb des EMA liegendes
Stressregime erzeugt nicht jeden Tag ein neues Signal. Dadurch entstehen von
2021 bis 2026 insgesamt 106 Ereignisse, also ungefaehr 19 pro Jahr vor
Portfolio-Limits.

Getestet wurden XSP Put-Credit-Spreads mit Delta `-0,20 +/-0,015`, zehn Punkten
Breite, TP50, maximal zwei gleichzeitigen Positionen und 2.500 USD definiertem
Gesamtrisiko. Entry und Exit verwenden echte historische ThetaData-NBBO-Quotes,
6,60 USD Roundtrip-Gebuehren sind enthalten. Weil 50- bis 70-DTE-Weeklies am
historischen Entry meistens noch nicht gelistet waren, ist die praktisch
handelbare Hauptauswertung der naechste gelistete Standard-Monatsverfall. Die
tatsaechlichen mittleren Entry-DTE betragen dadurch rund 65, 75 und 84 Tage.

| Mindest-DTE | Closed / Open | TP50 | Gewinner | Netto-P/L | Realisierter DD | taeglicher MTM-DD | Schlechtester Trade | P/L nach 0,10 Slippage |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | 45 / 0 | 37 | 40 | -1.964,50 USD | -2.714,60 USD | -2.882,90 USD | -906,60 USD | -2.414,50 USD |
| 60 | 46 / 1 | 40 | 43 | -20,60 USD | -1.208,90 USD | -1.888,40 USD | -908,60 USD | -480,60 USD |
| 70 | 43 / 1 | 36 | 41 | +509,20 USD | -950,60 USD | -1.697,20 USD | -950,60 USD | +79,20 USD |

Die hohe Trefferquote bestaetigt nur den ersten Teil der Hypothese: Der VIX kehrt
haeufig zurueck und TP50 wird meistens erreicht. Sie widerlegt aber die Annahme,
dass sich praktisch immer ein profitabler Exit ergibt. Bei 60 Mindest-DTE
erreichen sechs abgeschlossene Trades TP50 nicht; bei 70 Mindest-DTE sind es
sieben. Je ein nahezu voller Spreadverlust macht 2022 und 2025 trotz vieler
Gewinner negativ. Bei 60 Mindest-DTE ist der Bruttovorteil positiv, wird aber von
303,60 USD Gebuehren vollstaendig aufgezehrt. Die 70-DTE-Variante besitzt nur
einen moderaten Profit-Faktor von 1,27 und verliert bereits bei 0,20 Punkten
zusaetzlicher Roundtrip-Slippage ihren positiven Erwartungswert.

Ein Kontrolllauf mit dem global naechsten XSP-Verfall ab exakt 50/60/70 Tagen
liefert lediglich 7/5/2 handelbare Strukturen. Die betreffenden Weeklies waren
historisch zum Entry meist noch nicht gelistet. Diese winzige Gewinnerstichprobe
darf nicht als bessere Strategie interpretiert werden.

Urteil: Das reine EMA-Ueberschreiten erhoeht die Frequenz, verbessert den
wirtschaftlichen Edge aber nicht. Es kauft haeufig in einen noch steigenden VIX
und damit in fallenden SPX hinein; genau die fehlende Reversal-Bestaetigung war
der Schutz der urspruenglichen Variante. 50 und 60 Mindest-DTE werden verworfen.
70 Mindest-DTE bleibt hoechstens ein Research-Hinweis, ist mit P/L zu MTM-DD von
nur rund 0,30, zwei negativen Jahren und unvollstaendiger Quoteabdeckung nicht
paperreif. Keine Bot-Integration und keine Aenderung an Version 1.0.1.

Der logisch naechste Frequenztest ist kein weiteres Exit-Tuning, sondern eine
weichere, vorab definierte Reversal-Bestaetigung: VIX ueber EMA20 und mindestens
15, danach der erste niedrigere 13:35-ET-VIX-Wert beziehungsweise ein kleiner
Rueckgang vom lokalen Peak. Damit bleibt die fachliche Ursache der bisherigen
Stabilitaet erhalten, ohne zwingend auf einen vollen Ein-Punkt-Rueckgang zu
warten.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\test_xsp_vix_above_ema20_put_credit_proxy.py`
- `D:\PycharmProjects\PythonProject\research\backtest_xsp_vix_ema20_cross_dte_sweep_thetadata.py`
- `D:\PycharmProjects\PythonProject\research\plot_xsp_vix_ema20_cross_dte_sweep.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_ema20_cross_dte50_60_70_tp50_thetadata_v1\exact_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_ema20_cross_dte50_60_70_tp50_thetadata_v1\exact_yearly.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_ema20_cross_dte50_60_70_tp50_thetadata_v1\xsp_vix_ema20_cross_tp50_dte_comparison.png`

## 28. Kausale Peak-Bestaetigung auf der VIX-Kurve vom 2026-08-18

Als Reaktion auf das schlechte Verhalten des direkten EMA20-Aufwaertseinstiegs
wurde der Entry zunaechst ohne Options-P/L untersucht. Datengrundlage sind die
VIX-Werte um `13:35 ET` vom 2021-08-16 bis 2026-08-14. Ein Stressereignis beginnt,
wenn VIX mindestens 15 und oberhalb seines EMA20 liegt. Pro Ereignis wird nur ein
kausales Signal erzeugt. Als Fehl-Peak gilt ein Einstieg, nach dem innerhalb
desselben Stressereignisses noch ein hoeherer VIX-Wert folgt.

Fruehe Signale wie der erste niedrigere Wert oder nur 0,5 Punkte Rueckgang sind
ungeeignet: Bei einem Mindest-Peak von 19 liegen ihre Fehl-Peak-Raten bei 44,7 %.
Ein Rueckgang um einen Punkt reduziert sie auf 31,9 %. Der beste einfache
Frequenz-/Risiko-Kompromiss ist vorlaeufig:

- laufender VIX-Peak mindestens 19;
- VIX weiterhin mindestens 15;
- Einstieg erst nach mindestens zwei Punkten Rueckgang vom laufenden Peak;
- ein Signal je Stressereignis.

Diese Regel erzeugt 45 Signale beziehungsweise 9,1 Signale pro Jahr. Die
Fehl-Peak-Rate betraegt 15,6 % ueber die gesamten fuenf Jahre und 5,0 % in der
separat ausgewiesenen Periode 2024-2026. Nach fuenf und zehn Sitzungen liegt VIX
in jeweils 68,9 % beziehungsweise 66,7 % der Faelle niedriger. Der mediane
Rueckgang nach zehn Sitzungen betraegt 1,61 Punkte, der mediane beste Rueckgang
innerhalb dieser zehn Sitzungen 2,43 Punkte. Das 90%-Quantil eines zwischenzeitlichen
Wiederanstiegs liegt trotzdem bei 7,90 Punkten. Besonders 2022 zeigt, dass eine
einmalige Peak-Bestaetigung keinen erneuten Stressschub ausschliesst.

Ein Mindest-Peak von 21 erhoeht die Zehn-Tage-Trefferquote auf 73,5 %, senkt die
Frequenz aber auf 6,9 Signale pro Jahr und beseitigt das Wiederanstiegsrisiko
nicht. Zwei aufeinanderfolgende niedrigere VIX-Werte sind kein Ersatz fuer die
Mindest-Rueckgangshoehe. Ein Unterschreiten des EMA20 bestaetigt den lokalen Peak
zwar spaet und sauber, besitzt fuer die anschliessenden zehn Sitzungen allein
aber keine bessere Richtungsprognose.

Urteil: `Peak >= 19 / Rueckgang >= 2` ist der primaere Kandidat fuer den naechsten
Exact-Quote-Test. Er wird noch nicht in den Bot uebernommen. Vor einer Freigabe
muss er gegen `Rueckgang >= 1` und `Rueckgang >= 10 %` mit denselben echten
XSP-Quotes, 50/60/70 DTE und identischem Exit-Management verglichen werden. Der
Vergleich muss insbesondere die verlorene Entry-Praemie gegen das geringere
False-Peak- und MTM-Risiko stellen.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\analyze_vix_peak_reversal_entries_5y.py`
- `D:\PycharmProjects\PythonProject\research\analyze_vix_peak_confirmation_grid_5y.py`
- `D:\PycharmProjects\PythonProject\tests\test_vix_peak_confirmation_grid_5y.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_peak_reversal_entry_analysis_5y_v1\peak_confirmation_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\vix_peak_reversal_entry_analysis_5y_v1\vix_peak19_drop2_timeline_5y.png`

## 29. Kausaler SPX-Wochenfilter vom 2026-08-18

Fuer den Kandidaten `VIX-Peak >= 19 / Rueckgang >= 2` wurde ein einfacher,
vorab definierter Wochenfilter untersucht. Das Baerenregime ist aktiv, wenn der
letzte abgeschlossene SPX-Wochenschluss unter seinem EMA20 liegt und der EMA20
gegenueber vier abgeschlossenen Wochen zuvor gefallen ist. Eine Freitagsentscheidung
darf den Schluss derselben Woche nicht verwenden; jede Signalzeile wurde deshalb
gegen die tatsaechlich vorher verfuegbare Wochenzeile geprueft.

Der Filter sperrt sieben der 45 VIX-Signale: fuenf im Jahr 2022 sowie je eines
2023 und 2025. Auf der reinen VIX-Nachlaufstatistik sinkt die Fehl-Peak-Rate von
15,6 % auf 10,5 %, die Wahrscheinlichkeit eines nach zehn Sitzungen niedrigeren
VIX steigt aber nur von 66,7 % auf 68,4 %. Der mittlere maximale Wiederanstieg
innerhalb zehn Sitzungen verbessert sich von 2,57 auf 2,22 VIX-Punkte; der
schlechteste Wiederanstieg bleibt unveraendert bei 16,51 Punkten. Der Filter
entfernt zugleich ausgepraegt profitable Entspannungsbewegungen wie 2025-04-09.

Die wirtschaftliche Kontrolle verwendet echte historische ThetaData-NBBO-Quotes:
XSP Standard-Monatsverfall mit mindestens 70 DTE, Short-Put Delta `-0,20 +/-0,015`,
zehn Punkte Breite, TP50, maximal zwei Positionen und 2.500 USD definiertes
Gesamtrisiko. 34 der 45 Signale besitzen eine historische Struktur, 33 bestehen
die Delta-Toleranz. Nach Portfoliolimits werden ohne Filter 26 und mit hartem
Wochenfilter 25 Positionen abgeschlossen.

| Variante | Closed | Gewinner | Netto-P/L | Realisierter DD | Qualitaets-MTM-DD | Schlechtester Trade | P/L nach 0,10 Slippage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ohne Wochenfilter | 26 | 26 | +1.304,40 USD | 0,00 USD | -878,60 USD | +38,40 USD | +1.044,40 USD |
| Wochen-Baerenregime ausgesetzt | 25 | 25 | +1.245,50 USD | 0,00 USD | -878,60 USD | +38,40 USD | +995,50 USD |

Von den sieben gesperrten Signalen sind wegen historischer Quoteverfuegbarkeit
nur drei exakt bewertbar. Zwei davon werden bereits vom Zwei-Positionen-Limit
blockiert. Der einzige tatsaechlich entfernte Portfoliotrade vom 2022-04-28
erreicht TP50 und verdient netto 58,90 USD. Der harte Filter reduziert daher den
Gewinn, verbessert den MTM-Drawdown nicht und loest das verbleibende Extremrisiko
nicht.

Urteil: Der SPX-Wochenfilter wird verworfen und weder in die Signalregel noch in
den Bot uebernommen. Das Ergebnis stuetzt die Sorge, dass ein langsamer
Trendfilter zu viele profitable kurzfristige Vola-Normalisierungen ausblendet.
Der naechste sachlich passende Regimekandidat ist die VIX-Terminstruktur, weil
sie direkt zwischen kurzfristigem Stress beziehungsweise Backwardation und einer
normaleren Volatilitaetskurve unterscheidet.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\analyze_vix_peak19_drop2_weekly_spx_filter.py`
- `D:\PycharmProjects\PythonProject\research\backtest_xsp_vix_peak19_drop2_weekly_filter_thetadata.py`
- `D:\PycharmProjects\PythonProject\tests\test_vix_peak19_drop2_weekly_spx_filter.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_peak19_drop2_weekly_spx_filter_dte70_tp50_thetadata_v1\weekly_filter_exact_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_peak19_drop2_weekly_spx_filter_dte70_tp50_thetadata_v1\weekly_filter_exact_comparison.png`

## 30. IBKR-Terminstrukturdaten vom 2026-08-18

Die TWS-Contract-Discovery auf Port 7496 bestaetigt historische Cboe-Indexdaten
fuer `VIX9D`, `VIX`, `VIX3M`, `VIX6M` und `VIX1Y`. Der alte Alias `VXV` wird von
IBKR nicht aufgeloest; fuer die konstante Drei-Monats-Laufzeit ist `VIX3M` zu
verwenden. Laut `reqHeadTimeStamp` reicht VIX3M bis 2009-08-12, VIX bis
2005-10-03 und VIX9D/VIX6M/VIX1Y bis 2018-06-22 zurueck.

Der bestehende Rohdaten-Downloader fuehrt die vier zusaetzlichen Indizes jetzt
als eingebaute Assets. Der initiale Backfill hat Fuenf-Minuten-Daten vom
2021-02-22 bis 2026-08-18 geladen:

| Index | Bars | Duplikate | fehlende Close-Werte |
| --- | ---: | ---: | ---: |
| VIX9D | 107.147 | 0 | 0 |
| VIX3M | 111.283 | 0 | 0 |
| VIX6M | 107.151 | 0 | 0 |
| VIX1Y | 107.152 | 0 | 0 |

Aus den Rohdaten werden zwei kausal ausgerichtete Tagesmatrizen erzeugt: exakt
um `12:35 CT = 13:35 ET` sowie nach dem letzten vollstaendigen Tagesbar. Beide
enthalten 1.368 gemeinsame, vollstaendige Sitzungen vom 2021-02-22 bis
2026-08-17. Der laufende 2026-08-18 wird korrekt nicht als abgeschlossener Tag
ausgegeben. Enthalten sind alle fuenf Laufzeiten, `VIX/VIX3M`, `VIX3M-VIX`, ein
Backwardation-Kennzeichen sowie die benachbarten Laufzeitverhaeltnisse.

Damit ist der Datenpfad fuer den naechsten, noch nicht durchgefuehrten
Terminstrukturtest vollstaendig. Es wurde noch keine Schwelle anhand des
Options-P/L ausgewaehlt und keine Bot-Regel veraendert.

Artefakte:

- `D:\PycharmProjects\PythonProject\Daten_Download\probe_vix_term_structure_indices.py`
- `D:\PycharmProjects\PythonProject\Daten_Download\Download_Asset_Data.py`
- `D:\PycharmProjects\PythonProject\Daten_Download\build_vix_term_structure_daily.py`
- `D:\Trading\Rohdaten\vix_term_structure_ibkr_probe.csv`
- `D:\Trading\Rohdaten\VIX_term_structure_1335ET.csv`
- `D:\Trading\Rohdaten\VIX_term_structure_daily_close.csv`

## 31. Einfacher Terminstrukturfilter vom 2026-08-18

Als erster kausaler Test wurde die VIX-Terminstruktur exakt zum Entryzeitpunkt
`13:35 ET` nur binaer ausgewertet. Dabei wurden zwei verschiedene Begriffe
getrennt: die zeitliche Richtung des Frontverhaeltnisses `VIX/VIX3M` gegenueber
der vorherigen Sitzung sowie die Form der Kurve als Contango (`VIX < VIX3M`)
oder Backwardation (`VIX > VIX3M`). Alle 45 Peak19/Drop2-Signale besitzen einen
vollstaendigen Terminstruktur-Snapshot; der verwendete Vergleichswert stammt
nachweislich aus einer frueheren Sitzung.

Die zeitliche Richtung ist kein unabhaengiger Filter: Bei allen 45 Signalen
faellt `VIX/VIX3M`, bei keinem steigt es. Das ist inhaltlich plausibel, weil die
Signalregel bereits einen Rueckgang des kurzfristigen VIX um mindestens zwei
Punkte verlangt, waehrend VIX3M traeger reagiert. Eine zusaetzliche Regel
"Terminstruktur fallend" reproduziert deshalb exakt den ungefilterten Backtest.

Die Kurvenform teilt die Signale in 40 Contango- und fuenf
Backwardation-Sitzungen. In der reinen VIX-Nachlaufstatistik ist der VIX nach
zehn Sitzungen bei 65,0 % der Contango- und bei vier von fuenf
Backwardation-Signalen niedriger. Die Backwardation-Stichprobe ist fuer eine
allgemeine Aussage deutlich zu klein.

Die wirtschaftliche Kontrolle verwendet unveraendert die exakten historischen
ThetaData-NBBO-Strukturen: XSP Standard-Monatsverfall mit mindestens 70 DTE,
Short-Put Delta `-0,20 +/-0,015`, zehn Punkte Breite, TP50, maximal zwei
Positionen und 2.500 USD definiertes Gesamtrisiko.

| Variante | Signale | Closed | Gewinner | Netto-P/L | Qualitaets-MTM-DD | P/L nach 0,10 Slippage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ohne Filter | 45 | 26 | 26 | +1.304,40 USD | -878,60 USD | +1.044,40 USD |
| `VIX/VIX3M` fallend | 45 | 26 | 26 | +1.304,40 USD | -878,60 USD | +1.044,40 USD |
| `VIX/VIX3M` steigend | 0 | 0 | 0 | 0,00 USD | 0,00 USD | 0,00 USD |
| Contango | 40 | 23 | 23 | +1.176,70 USD | -878,60 USD | +946,70 USD |
| Backwardation | 5 | 3 | 3 | +127,70 USD | -513,00 USD | +97,70 USD |

Die fuenf Backwardation-Termine sind 2022-09-28, 2024-08-06, 2024-11-05,
2025-03-12 und 2025-04-09. Fuer 2022-09-28 fehlt die historische Long-Leg-Quote;
am 2025-04-09 war der konservativ berechnete Credit nicht positiv. Die drei
handelbaren Strukturen erreichten alle TP50. Der Trade vom 2025-03-12 hatte
trotzdem zwischenzeitlich eine einzelne Trade-MAE von -889,30 USD; die
qualitaetsgefilterte taegliche Portfoliokurve dieses Teilsets erreicht -513,00
USD Drawdown.

Urteil: Weder "fallend" noch eine einfache Contango-Sperre werden in den Bot
uebernommen. Der Richtungsfilter hat definitionsbedingt keine Trennwirkung. Eine
Backwardation-Sperre wuerde in den vorhandenen exakten Daten profitable Trades
entfernen, den gesamten Qualitaets-MTM-Drawdown nicht verbessern und beruht nur
auf drei bewertbaren Positionen. Ein spaeterer Terminstrukturtest sollte daher
die Staerke und Persistenz der Normalisierung messen, nicht dieselbe binaere
Information erneut verwenden.

Artefakte:

- `D:\PycharmProjects\PythonProject\research\analyze_xsp_vix_peak19_drop2_term_structure_direction.py`
- `D:\PycharmProjects\PythonProject\tests\test_xsp_vix_peak19_drop2_term_structure_direction.py`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_peak19_drop2_term_structure_direction_v1\signal_direction_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_peak19_drop2_term_structure_direction_v1\exact_direction_summary.csv`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_peak19_drop2_term_structure_direction_v1\term_structure_direction_exact_comparison.png`
- `D:\PycharmProjects\PythonProject\data\option_omega_15DTE_ruleset1_analysis\xsp_vix_peak19_drop2_term_structure_direction_v1\term_structure_shape_exact_comparison.png`
