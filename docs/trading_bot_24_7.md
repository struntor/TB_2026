# Trading-Bot 24/7 Supervisor

Für den täglichen manuellen Start wird nicht der Live-Bot direkt aufgerufen,
sondern [`manual_trading_day.md`](manual_trading_day.md) beziehungsweise
`scripts\run_trading_day.py`. Dieser Einstieg führt einmal dieselbe vollständige
Pipeline aus und beendet sich nach dem Handelsschluss.

Der Supervisor hält den täglichen Produktionsablauf dauerhaft aktiv. Er verwendet
weiterhin die bestehenden, einzeln testbaren Programme und führt sie sequenziell
aus:

1. abgeschlossene TWS-Ausführungen seit dem vorherigen US-Handelstag ins Journal
   übernehmen;
2. `Download_Asset_Data.py` starten und anschließend alle 50 Produktionssymbole
   auf einen vollständigen letzten Handelstag prüfen;
3. den Manual-Regime-Backtest mit 24 Workern bis zum Vortag ausführen;
4. die exakt erzeugte Backtest-Run-ID an den Live-Bot übergeben;
5. Signale nach 09:40 ET berechnen und – nur mit `--place-orders` – an die TWS
   senden;
6. aktivierte Optionsmonitore bis zum Handelsschluss betreiben und danach auf den
   nächsten NYSE-Handelstag warten.

## Paper-Trading starten

TWS muss laufen, API-Verbindungen auf `127.0.0.1:7496` erlauben und mit dem
Paperkonto verbunden sein.

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_bot_24_7.py `
  --port 7496 `
  --workers 24 `
  --place-orders `
  --enable-failed-auction-fly `
  --enable-spxw-15dte-put-credit `
  --enable-spxw-0dte-bull-put-iu30 `
  --enable-spxw-75dte-put-bwb `
  --spxw-75dte-bwb-initial-entry-work-minutes 5 `
  --spxw-75dte-bwb-entry-work-minutes 5 `
  --spxw-75dte-bwb-final-entry-work-minutes 5 `
  --enable-xsp-vix-pullback `
  --xsp-vix-pullback-entry-work-minutes 10 `
  --enable-stock-ivr70-put-credit `
  --options-account-value-usd 25000 `
  --options-max-risk-fraction 0.10
```

Ohne `--place-orders` wird derselbe Ablauf als Dry-Run ausgeführt. Der
Paperkonto-Guard bleibt standardmäßig aktiv; ein Livekonto wird ohne
`--allow-live-account` abgewiesen.

`--enable-spxw-0dte-bull-put-iu30` aktiviert die eingefrorene kausale
09:40-ET-Variante. Sie verwendet die bereits berechneten Top50-/SPX-Kontexte,
sendet höchstens einen atomaren SPXW-Comboauftrag pro Tag und teilt den
definierten Options-Risikodeckel mit den anderen Credit-Spread-Strategien. Der
Auftrag muss bis 09:50 ET erzeugt sein; ein späterer Lauf wird als Timing-Veto
journalisiert. Der
Schalter allein erzeugt nur einen Journal-Dry-Run; eine Paperorder benötigt
zusätzlich `--place-orders`, und dieser Strategiepfad akzeptiert ausschließlich
ein `DU`-Konto.

`--enable-spxw-75dte-put-bwb` aktiviert ab 03.09.2026 den täglichen Mo–Fr-Paper-Fill-Test ab 09:32 ET (VIX >= 15, normale Positions- und Risikolimits).

Beim Start prueft der Live-Bot alle spaeter benoetigten Log-, Journal-, Overlay-
und State-Verzeichnisse mit einem echten Schreib-/Flush-/Loeschtest. Ausserdem
wird der konfigurierte Windows-Prozesspool vor der TWS-Verbindung probeweise
erzeugt. Ist die Named-Pipe-Erzeugung nicht erlaubt oder fallen die
Prozesshandles spaeter aus, wechselt die Engine fuer diesen Lauf automatisch
auf unabhaengige Python-Worker ohne `multiprocessing.Pipe`, bei gleicher
Workerzahl und Symbolreihenfolge. Nur wenn auch `CreateProcess` blockiert ist,
dient ein Thread-Pool als letzte absturzsichere Reserve. Ein reiner
Diagnoselauf ohne TWS-Verbindung ist moeglich mit:

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe `
  D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py `
  --max-workers 24 `
  --preflight-only
```
Sie wählt den ersten gelisteten SPXW-Verfall mit mindestens 75 DTE, baut den
+35/-55 Put-Broken-Wing-Butterfly um den 1-%-OTM-Body und legt nach einem
Einstiegsfill eine inverse GTC-Combo-Order für 40 % Gewinn in die TWS. Auch
dieser Pfad ist hart auf `DU`-Paperkonten begrenzt und teilt den gemeinsamen
Options-Risikodeckel.

`--enable-xsp-vix-pullback` aktiviert den separat schaltbaren XSP-Paperpfad.
Er bewertet um 13:35 ET den kausalen VIX-Pullback (`Peak >= 19`, Rueckgang
mindestens einen Punkt, Entry-VIX mindestens 15, zehn Sitzungen Zeitfenster und
25 Kalendertage Cooldown) und verlangt zusaetzlich positives SPX-Momentum ueber
20 Sitzungen. Gehandelt wird ein standardmaessiger XSP-Monatsverfall mit
mindestens 100 DTE, Short-Delta `-0,20 +/- 0,015` und exakt zehn Punkten Breite.
Nach dem Fill liegt eine inverse GTC-Combo fuer TP60 in der TWS. Bei 60 Rest-DTE
wird sie erst nach bestaetigter Stornierung durch einen quote-geprueften
DAY-Schliessauftrag ersetzt. Der Pfad ist nur fuer `DU`-Paperkonten freigegeben
und teilt den 2.500-USD-Risikodeckel mit den anderen definierten
Optionsstrategien.

## Zeitplan und Wiederanlauf

- Vorbereitung beginnt standardmäßig um `05:00 ET`.
- Schlägt TWS, Download oder Backtest fehl, wird nach fünf Minuten erneut
  versucht.
- Nach `09:35 ET` werden bei einem verspäteten Neustart keine neuen Aktienorders
  für diesen Tag mehr gestartet.
- Erfolgreiche Phasen werden in
  `D:\Trading\journal\trading_bot_24_7_state.json` gespeichert und bei einem
  Neustart nicht doppelt ausgeführt.
- Eine Lockdatei verhindert zwei gleichzeitig laufende Supervisoren.
- Wochenenden und reguläre NYSE-Feiertage werden übersprungen.

Der vollständige Prozesslog liegt unter
`D:\Trading\logs\trading_bot_24_7.log`.

Monats- und Jahreskurven sowie der Vergleich des Referenz-Backtests mit den
tatsächlichen Paper/Live-Trades werden mit dem read-only Journalreport erzeugt.
Die Bedienung und Bedeutung der Kennzahlen beschreibt
[`trading_journal_reports.md`](trading_journal_reports.md).

Für den gemeinsamen Betrieb einer Paper- und einer Produktions-TWS gibt es den
expliziten, fail-closed Parallelmodus. Kontoprüfungen, getrennte Orderfreigaben
und die Startbefehle für den Paper-Regressionstest und den Parallelbetrieb sind
in [`paper_production_parallelbetrieb.md`](paper_production_parallelbetrieb.md)
dokumentiert.

## Einmaliger Recovery-/Testlauf

Ein konkreter Handelstag kann bewusst sofort ausgeführt werden:

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_bot_24_7.py --once-day 2026-07-27
```

Auch dieser Modus ist ohne `--place-orders` ein Dry-Run. Bereits als erfolgreich
gespeicherte Phasen werden ausgelassen. Einzelne Phasen können für Diagnosezwecke
mit `--skip-journal-sync`, `--skip-data-download` oder `--skip-backtest`
übersprungen werden; die erforderlichen Rohdaten beziehungsweise das passende
Backtest-Manifest müssen dann bereits existieren.

## Grenzen

Der Supervisor ersetzt weder TWS noch deren Anmeldung. Nach einem Windows-Neustart
müssen TWS und der Supervisor erneut gestartet werden, sofern sie nicht über die
Windows-Aufgabenplanung automatisch gestartet werden. Unerwartete einmalige
Börsenschließungen sind nicht im eingebauten Regelkalender hinterlegt; in diesem
Fall verhindert die Rohdatenprüfung den Handel und der Vorgang wird protokolliert.
