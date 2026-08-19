# Multi-Stock IVR70 Put-Credit – Paper-Runbook

## Eingefrorene Variante

- Strategy-ID: `multi-stock-ivr70-put-credit`
- Variante: `multi-stock-ivr70-put-credit@1.0.1`
- Modus: ausschließlich IBKR-Paperkonten mit Präfix `DU`
- Universum: liquide US-Aktien, Kurs mindestens 80 USD, 60-Tage-Median-Dollarvolumen mindestens 100 Mio. USD
- Signal: kausaler modellierter IVR-Cross von höchstens 70 auf über 70 am letzten abgeschlossenen Handelstag
- Einstieg: nächster verfügbarer Handelstag zwischen 09:40 und 10:15 America/New_York
- Struktur: ein Put-Credit-Spread, 35–50 Kalendertage, Ziel 42 DTE, Short-Delta-Ziel -0,19 innerhalb der harten Spanne 0,16–0,22, Breite 10 Punkte
- Earnings: kein Einstieg, wenn der kausal prognostizierte Termin spätestens sieben Tage nach dem konkret gewählten Verfall liegt
- Exit: 50 % Credit-Gewinn oder zwingend bei 21 verbleibenden Kalendertagen; Prüfung einmal täglich 15:50–15:55 ET
- Auswahl bei konkurrierenden Signalen: kleinster definierter Maximalverlust, dann höchste kausale 60-Tage-Dollarliquidität, dann Symbol
- Gemeinsamer Options-Risikodeckel: standardmäßig 10 % eines modellierten 25.000-USD-Kontos, also 2.500 USD einschließlich geschätzter Roundtrip-Gebühren

Die Strategie ist nicht für Livekonten freigegeben. `--allow-live-account` hebt die separate DU-Sperre nicht auf. Der Risikodeckel ist derzeit ein fester USD-Modellwert; für ein echtes EUR-Konto ist vor Produktionsfreigabe eine dokumentierte Währungsumrechnung erforderlich.

## Dry-Run

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --enable-stock-ivr70-put-credit --account DU_PAPER_ACCOUNT --port 7497 --max-workers 24
```

Der Scanner erzeugt zunächst aus den bereits aktualisierten Rohdaten die Tageskandidaten. Entscheidung, Faktoren, Optionsstruktur, Quotes, Griechen und ein expliziter `dry_run_no_order`-Blocker werden ins Journal geschrieben. Es wird keine Order gesendet.

## Zweite Optionsstrategie gemeinsam mit SPXW testen

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --enable-spxw-15dte-put-credit --enable-stock-ivr70-put-credit --place-orders --account DU_PAPER_ACCOUNT --port 7497 --max-workers 24 --options-account-value-usd 25000 --options-max-risk-fraction 0.10
```

Beide Strategien bleiben unabhängig schaltbar, teilen aber denselben Deckel für definiertes offenes Optionsrisiko. `--place-orders` gilt ebenfalls für die Aktienstrategie des gemeinsamen Runners. Vor dem Start müssen deshalb Paper-TWS, API-Port und das aktualisierte Verzeichnis `D:\Trading\Rohdaten` geprüft sein.

## Persistenz und Kontrolle

- Journal: `D:\Trading\journal\trading_journal.sqlite`
- Strategie-State: `D:\Trading\state\stock_ivr70_put_credit_v1.json`
- Kandidaten: `D:\Trading\logs\stock_ivr70_live_candidates_YYYYMMDD.csv`
- Scanner-Diagnostik: `D:\Trading\logs\stock_ivr70_live_candidates_YYYYMMDD_diagnostics.csv`
- Entry-OrderRef: `IVR70-E-SYMBOL-SIGNALTAG`
- Exit-OrderRef: `IVR70-X-SYMBOL-SIGNALTAG`

Nicht gefüllte Entries werden nach fünf Minuten storniert. Die Order wird als BAG-Limit gesendet. Der Persistenz-State verhindert doppelte Entries, hält auch ungeklärte Assignment-Fälle im Risikodeckel und speichert die qualifizierten ConIds für die spätere Exit-Reconciliation.

## Verifikationsstand 2026-07-22

- Portfolio-Backtest 2021–20.07.2026: 174 Trades, +2.486,69 USD, 73,56 % Gewinner, MaxDD 1.350,73 USD, maximales offenes Risiko 2.498,60 USD.
- Jährlich 2022–2026 positiv; 2021 mit nur neun Warm-up-Trades negativ.
- Live-Scanner-Parität: CTAS-Signal 02.07.2026 reproduziert IVR 78,640614, Entry 06.07.2026 und Earnings-Prognose 23.09.2026 exakt.
- Dreizehn IVR70-Unit-Tests bestanden; neun relevante SPXW-Regressionsprüfungen bestanden.
- Version 1.0.1 trennt `no_expiration_in_dte_window`, `earnings_after_expiry_buffer`, `no_exact_width_pair`, `no_short_delta_in_range`, `no_positive_conservative_credit` und echte fehlende Marktdaten im Journal.
- Runner-CLI und alle neuen Module kompilieren/importieren.
- Ausstehend: vollständiger Tages-Dry-Run an laufender Paper-TWS, erster BAG-Paper-Fill, Exit-Fill sowie Journal-/Broker-Reconciliation.
