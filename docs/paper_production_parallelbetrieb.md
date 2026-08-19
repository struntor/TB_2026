# Paper-/Produktions-Parallelbetrieb

Der Supervisor kann eine gemeinsame Tagesvorbereitung und eine gemeinsame
Signalberechnung für zwei strikt getrennte TWS-Verbindungen verwenden:

- Das bisherige Hauptziel bleibt das erforderliche Paperkonto. Es erhält die
  Multi-Asset-Regime-Trades und alle ausdrücklich aktivierten
  Paper-Optionsstrategien.
- Das optionale Produktionsziel erhält ausschließlich die freigegebene
  Multi-Asset Daily Regime Strategie. Optionsmonitore werden niemals an diese
  Verbindung gebunden.
- Rohdaten-Download, Backtest, Live-Kursabruf und Regime-Berechnung laufen nur
  einmal. Derselbe berechnete Stock-Tradeplan wird anschließend kontogetrennt
  journalisiert und geroutet.

## Sicherheitsmodell

Der Parallelbetrieb muss mit `--dual-broker` ausdrücklich aktiviert werden.
Zusätzlich sind die exakten Kontonummern und beide Broker-Endpunkte Pflicht.

- `--account` muss ein `DU...`-Paperkonto sein.
- `--production-account` muss ein Nicht-`DU`-Konto sein.
- Die von TWS gemeldete Kontonummer muss exakt zum jeweiligen Parameter passen.
- `--place-orders` erlaubt ausschließlich Paperorders.
- `--production-place-orders` ist die separate Freigabe für echte Orders.
- `--allow-live-account` ist im Parallelbetrieb verboten.
- Die Produktionsverbindung wird beim Start und unmittelbar vor dem Routing
  erneut geprüft. Bei Fehler, falschem Konto oder Verbindungsabbruch bleibt der
  Produktionspfad gesperrt; die Paper-Pipeline läuft weiter.
- Es gibt keinen Fallback auf einen anderen Port oder ein anderes Konto.

Der read-only Journal-Sync wird zuerst für Paper und danach separat für
Produktion ausgeführt. Ein nicht erreichbares Produktionsziel erhält im
Pipeline-State den Status `unavailable`; es stoppt Paper nicht. Rohdaten und
Backtest bleiben gemeinsame einmalige Phasen. Für diesen Tageszyklus bleibt
Produktion dann vollständig deaktiviert, selbst wenn der Port später wieder
erreichbar wird. Erst ein erfolgreicher Produktions-Sync gibt den späteren
Produktions-Routingpfad frei. `--skip-journal-sync` sperrt Produktion im
Parallelmodus daher ebenfalls.

## Test 1: unveränderter Paper-Tageslauf

Für den ersten Regressionstest werden keinerlei neue Produktionsschalter
gesetzt. Damit läuft exakt der bisherige Ein-Konto-Pfad:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
$PaperAccount = "DU..."

D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_day.py `
  --host 127.0.0.1 `
  --port 7496 `
  --account $PaperAccount `
  --workers 24 `
  --place-orders `
  --enable-failed-auction-fly `
  --enable-spxw-15dte-put-credit `
  --enable-spxw-0dte-bull-put-iu30 `
  --enable-spxw-75dte-put-bwb `
  --enable-xsp-vix-pullback `
  --enable-stock-ivr70-put-credit
```

Vor dem Start ist zu kontrollieren, dass Port `7496` tatsächlich die TWS mit
dem angegebenen `DU...`-Konto ist.

## Test 2: erster Parallelbetrieb

Die Platzhalter müssen vor dem Start durch die Kontonummern ersetzt werden.
Auch die Portzuordnung ist in beiden TWS-Instanzen zu kontrollieren; der Code
leitet die Konten absichtlich nicht aus den üblichen IBKR-Standardports ab.

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
$PaperAccount = "DU..."
$ProductionAccount = "U..."

D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_day.py `
  --host 127.0.0.1 `
  --port 7496 `
  --sync-client-id 63 `
  --live-client-id 62 `
  --account $PaperAccount `
  --place-orders `
  --dual-broker `
  --production-host 127.0.0.1 `
  --production-port 7497 `
  --production-sync-client-id 73 `
  --production-live-client-id 72 `
  --production-account $ProductionAccount `
  --production-place-orders `
  --workers 24 `
  --enable-failed-auction-fly `
  --enable-spxw-15dte-put-credit `
  --enable-spxw-0dte-bull-put-iu30 `
  --enable-spxw-75dte-put-bwb `
  --enable-xsp-vix-pullback `
  --enable-stock-ivr70-put-credit
```

Ohne `--production-place-orders` wird der Produktions-Tradeplan lediglich als
Live-Dry-Run journalisiert. Ohne `--place-orders` bleibt Paper ebenfalls ein
Dry-Run. Die beiden Freigaben sind unabhängig.

## Kontrolle im Log und Journal

Im Prozesslog müssen beim Parallelstart unter anderem diese Aussagen erscheinen:

- `production TWS validated`
- `routing approved manual-regime stock plan to production`
- oder im Fehlerfall `production ... disabled` beziehungsweise
  `production routing failed closed`

Der gemeinsame Tradeplan steht weiterhin in
`D:\Trading\logs\manual_regime_live_trades_YYYYMMDD.csv`. Im SQLite-Journal
entstehen getrennte Strategie-Runs: `paper` mit dem `DU...`-Konto und `live`
mit dem Produktionskonto. Beide verwenden dieselben Signal-Keys und dieselbe
Backtest-Run-ID, wodurch Paper, Produktion und Modellhistorie direkt verglichen
werden können.

Relevante Betriebsdateien:

- Prozesslog: `D:\Trading\logs\trading_bot_24_7.log`
- Pipeline-State: `D:\Trading\journal\trading_bot_24_7_state.json`
- Journal: `D:\Trading\journal\trading_journal.sqlite`
