# SPX 15DTE Put-Credit – Paper-Runbook

## Freigegebene Variante

- Strategy-ID: `spx-15dte-put-credit-income-core`
- Variante: `spx-15dte-put-credit-income-core@1.1.0`
- Modus: ausschließlich IBKR-Paperkonten mit Präfix `DU`
- Signal: Schluss der vollständig abgeschlossenen 13:30-SPY-Kerze
- Optionsquote und frühester Entry: 13:35 America/New_York
- Struktur: ein SPXW-Put-Credit-Spread, Ziel-Delta -0,25, Breite 10 Punkte, Ziel 15 Kalendertage
- Positionsgrenze: höchstens sechs parallele Positionen, je ein Kontrakt

Die Strategie ist nicht für Livekonten freigegeben. Auch `--allow-live-account` hebt die separate DU-Sperre dieser Strategie nicht auf.

## Sicherer Dry-Run

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --enable-spxw-15dte-put-credit --account DU_PAPER_ACCOUNT --port 7497 --max-workers 24
```

Dabei werden Signale, Struktur, Quote, Griechen und ein expliziter Ausführungs-Blocker ins Journal geschrieben. Es wird keine Order gesendet.

## Paper-Order aktivieren

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --enable-spxw-15dte-put-credit --place-orders --account DU_PAPER_ACCOUNT --port 7497 --max-workers 24
```

`--place-orders` gilt auch für die bereits vorhandene Aktienstrategie des gemeinsamen Runners. Vor dem Start deshalb prüfen, dass die TWS im Paperkonto angemeldet ist und der angegebene API-Port stimmt.

## Persistenz und Kontrolle

- Journal: `D:\Trading\journal\trading_journal.sqlite`
- Strategie-State: `D:\Trading\state\spxw_15dte_put_credit_v1.json`
- Entry-OrderRef: `SPX15-E-YYYYMMDD`
- Damage-Exit-OrderRef: `SPX15-X-YYYYMMDD`

Nicht gefüllte Entries werden nach fünf Minuten storniert. Nach Neustarts werden ConIds, OrderRefs und vorhandene SPXW-Exposures abgeglichen; unbekannte Exposures sperren einen neuen Entry. Der Damage-Exit wird nach bestätigtem 16:00-Signal am nächsten Handelstag zwischen 09:35 und 09:45 als begrenzte Combo-Order versucht. Bei einem geschätzten Bid/Ask-Friction-Gap über 100 USD wird er verworfen und die Position bleibt bis zur Fälligkeit im Strategie-State.

## Verifikationsstand 2026-07-21

- Kausaler Replay: 129 Trades, +10.109,60 USD nach Gebühren, Trefferquote 87,60 %, MaxDD -1.565,20 USD.
- Historische Live-Regel-Parität: 129 von 129 Trades.
- Unit-Tests: Signalzeit, Reentry, Paper-Sperre, Combo-Richtung, Limitpreise, State, Entry-Timeout sowie Journal-/Dry-Run-Pipeline bestanden.
- Ausstehend: erster read-only TWS-Vertragstest, echter BAG-Paper-Fill und Damage-Exit-Reconciliation. Bei der letzten Prüfung lauschte lokal kein TWS-API-Port.
