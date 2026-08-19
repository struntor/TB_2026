# XSP VIX-Pullback: Paper-Runbook

## Status

Die Strategie ist technisch als ueberwachter Paper-Kandidat integriert, nicht als
Produktionsfreigabe. Am 2026-08-11 wurde im urspruenglichen historischen Test ein
Zeitachsenfehler entdeckt: VIX wurde aus den lokal in CT gespeicherten CBOE-Daten
um 13:35 CT statt um 12:35 CT gelesen, waehrend die Optionsquotes von 13:35 ET
stammten. Die frueher berichtete Performance ist deshalb nicht mehr gueltig.
Version `1.0.1-paper` behebt den Zeitstempel; bis zur erneuten Exact-Quote-
Verifikation ist der Pfad ausschliesslich experimentelles Paper-Monitoring.

## Vorlaeufig eingefrorene Regeln

- Entscheidung: 13:35 ET; neue Orders nur bis 13:50 ET. Historische CBOE-
  Indexbalken sind in den lokalen CSVs in CT gespeichert, daher entspricht der
  verwendete Rohdaten-Zeitstempel 12:35 CT.
- VIX-Zustandsmaschine: Peak mindestens 19, Rueckgang vom Peak mindestens 1,
  Entry-VIX mindestens 15, Reversal innerhalb von zehn Sitzungen, danach 25
  Kalendertage Cooldown.
- Filter: SPX um 13:35 ET liegt ueber dem SPX-Schlusskurs vor 20 Sitzungen.
- Struktur: XSP Bull-Put-Credit-Spread, erster tatsaechlich gelisteter
  Standard-Monatsverfall ab 100 Kalendertagen, Short-Delta `-0,20 +/- 0,015`,
  Long Put exakt zehn Punkte tiefer.
- Entry-Gate: konservativer Credit `Short Bid - Long Ask` muss positiv sein.
- Management: GTC-Rueckkauf bei 40 % des Entry-Credits (TP60); ab 60 Rest-DTE
  quote-gepruefter DAY-Rueckkauf. Beide Leg-Bids muessen positiv sein und der
  relative Spread jedes Legs darf hoechstens 20 % betragen.
- Risiko: ein Kontrakt, maximal zwei gleichzeitige Positionen und zusammen mit
  den anderen aktivierten definierten Optionsstrategien maximal 2.500 USD
  offenes definiertes Risiko bei 25.000 USD Modellkonto.
- Sicherheit: harter `DU`-Paperkonto-Guard.

## 24/7-Pipeline

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_bot_24_7.py `
  --port 7496 `
  --workers 24 `
  --place-orders `
  --enable-xsp-vix-pullback `
  --options-account-value-usd 25000 `
  --options-max-risk-fraction 0.10
```

Ohne `--place-orders` wird ein akzeptiertes Signal mitsamt Struktur im Journal
erfasst, aber keine Order an die TWS gesendet. Ohne
`--enable-xsp-vix-pullback` bleibt die Strategie vollstaendig inaktiv.

## Kontrolle

- Persistenter Zustand:
  `D:\Trading\state\xsp_vix_pullback_100dte_v1_0_1.json`
- Journal: `D:\Trading\journal\trading_journal.sqlite3`
- Order-Referenzen: `XSPVX-E-*`, `XSPVX-TP-*`, `XSPVX-T60-*`
- Nach dem Entry-Fill muessen zwei XSP-Legs und genau eine inverse GTC-Combo in
  der TWS sichtbar sein.
- Bei 60 Rest-DTE darf nie gleichzeitig die alte TP-Order und die neue
  Zeitstopp-Order aktiv sein. Ohne bestaetigte TP-Stornierung verweigert der
  Monitor den Zeitstopp-Auftrag.

## Monitoring-Entscheidung

Parameter werden nicht anhand der ersten Papertrades nachoptimiert. Verglichen
werden Signal, Delta, Credit, Fill, Haltedauer, Exitgrund, realisierte P/L und
zwischenzeitlicher Mark-to-Market-Verlauf. Die Strategie bleibt klein und
separat schaltbar. Vor einem weiteren historischen Performanceurteil muss der
korrigierte 12:35-CT-Signalsatz mit echten 13:35-ET-Optionsquotes neu berechnet
werden.
