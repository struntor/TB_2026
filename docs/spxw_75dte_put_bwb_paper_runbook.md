# SPXW 75DTE Put-BWB Paper-Runbook

## Eingefrorene Regeln

- Paper-Variante `1.5.0-paper-weekday-fill-experiment`: regulär Donnerstag,
  vorübergehend an jedem Börsen-Wochentag vom 14.08.2026 bis einschließlich
  27.08.2026; Entscheidung ab 09:32 ET;
- Tages-Open des VIX mindestens 15;
- erster aktuell gelisteter SPXW-Verfall mit mindestens 75 Kalendertagen DTE;
- Body am verfügbaren Strike, der 99 % des aktuellen SPX-Kurses am nächsten liegt;
- Long Put Body +35, zwei Short Puts am Body, Long Put Body -55;
- Einstieg zunächst zum aktuellen Struktur-Mid minus 0,20 Punkte;
- nach 5 Minuten ohne Fill wird dieselbe Order-ID auf den dann aktuellen Mid
  minus 0,40 Punkte geändert. Das neue Limit wird nie weniger marktgängig als
  das bisherige Limit;
- nach weiteren 5 Minuten ohne Fill wird dieselbe Order-ID auf den dann
  aktuellen Mid minus 0,65 Punkte geändert;
- die letzte Preisstufe darf weitere 5 Minuten arbeiten und wird danach
  storniert. Das gesamte Arbeitsfenster beträgt damit 15 Minuten;
- ein Kontrakt, definierter Maximalverlust `20 Punkte - tatsächlicher Credit`;
- nach dem Fill sofortige inverse GTC-Combo-Order bei 40 % Gewinn;
- ansonsten Verfall; ausschließlich Paperkonto mit Präfix `DU`.

## Befristetes Fill-Experiment 14.–27.08.2026

Für die nächsten zehn Börsensitzungen vom 14.08. bis 27.08.2026 wird der Entry
montags bis freitags geprüft. Der VIX-Open-Filter von mindestens 15 und
alle Struktur-, Preis- und Exitregeln bleiben aktiv. Nur innerhalb dieses
Datumsfensters gelten maximal 20 gleichzeitige BWB-Positionen und der separate
10.500-USD-BWB-Risikodeckel blockiert keine neue Paperorder. Der DU-Konto-Guard,
die atomare Combo-Order und der Duplikatschutz bleiben unverändert.

Die am 13.08.2026 separat eröffnete und eindeutig mit `SPX75-TEST-` markierte
Position darf das Experiment begleiten und blockiert die neuen Entries nicht.
Andere unbekannte `SPX75-`-Positionen oder Orders bleiben weiterhin ein hartes
Sicherheitsveto.

Ab dem 28.08.2026 läuft die Ausnahme automatisch aus. Der nächste mögliche
reguläre Einstieg ist dann wieder Donnerstag; es gelten wieder höchstens sieben
gleichzeitige Positionen und der normale BWB-Risikodeckel. Bestehende Positionen
und ihre GTC-Zielorders werden selbstverständlich weiter verwaltet.

## Dry-Run

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_day.py `
  --port 7496 `
  --workers 24 `
  --enable-spxw-75dte-put-bwb `
  --spxw-75dte-bwb-initial-entry-work-minutes 5 `
  --spxw-75dte-bwb-entry-work-minutes 5 `
  --spxw-75dte-bwb-final-entry-work-minutes 5 `
  --spxw-75dte-bwb-max-risk-usd 10500 `
  --options-account-value-usd 25000 `
  --options-max-risk-fraction 0.10
```

Der Dry-Run qualifiziert die echte Struktur und schreibt Entscheidung, Faktoren,
Quotes, Legs, Credit, Maximalverlust und `dry_run_no_order` ins Journal. Er sendet
keine Order.

## Paperorder

Dem Befehl zusätzlich `--place-orders` hinzufügen. Dieser Schalter gilt für
alle im gemeinsamen Runner aktivierten Strategien. Die Strategie verweigert
unabhängig von `--allow-live-account` jedes Konto ohne `DU`-Präfix.

Zustand: `D:\Trading\state\spxw_75dte_put_bwb_thursday_v1_2.json`  
Journal: `D:\Trading\journal\trading_journal.sqlite`

## Verifizierter Praxistest

Der isolierte Paper-Test vom 13.08.2026 wurde um 14:17:56 UTC beziehungsweise
16:17:56 CEST gefüllt. Der Credit betrug 4,10 Punkte; gehandelt wurden die
SPXW-Puts 7760/+1, 7725/-2 und 7670/+1 mit Verfall 30.10.2026. Danach lag genau
eine inverse GTC-Zielorder bei 2,45 Punkten Debit in der TWS. Dieser Test belegt
den atomaren Combo-Fill und die automatische 40-%-Zielorder. Die neue
5-plus-5-plus-5-Minuten-Preisstaffel ist zusätzlich durch automatisierte Lifecycle-
Tests abgesichert und wird nun im regulären Bot-Pfad paper-gehandelt.

Der BWB-Pfad besitzt einen eigenen Paper-Risikosleeve von standardmäßig
10.500 USD. Der allgemeine Optionsdeckel von 2.500 USD würde die getesteten bis
zu sieben überlappenden Wochenpositionen verhindern. 10.500 USD liegt knapp
oberhalb des historischen Spitzenrisikos von 10.115 USD. Diese Trennung ist
Paper-Research-Money-Management und keine Freigabe für ein Echtgeldkonto.

## Variantenhistorie

Der reproduzierte historische Referenztest und die Strategy-ID tragen weiterhin
`tuesday` als Herkunftsbezeichnung. Für den erneuten Fill-Test am 12.08.2026
wurde ausschließlich der Einstiegstag auf Mittwoch verschoben. Struktur,
VIX-Filter, Preislogik, Zielorder und Risikoregeln blieben unverändert. Der neue
State verhindert, dass bereits ausgewertete Dienstage den Mittwochslauf sperren.

Auch der Mittwochseinstieg vom 12.08.2026 blieb ungefüllt. Für den erneuten
Fill-Test am 13.08.2026 wurde ausschließlich der Einstiegstag auf Donnerstag
verschoben (`1.2.0-paper-thursday`). Der wiederum separate State verhindert,
dass der ausgewertete Mittwoch den Donnerstagseinstieg sperrt.

Der isolierte Donnerstagstest wurde bei einem deutlich marktgängigeren Credit
gefüllt. Version `1.4.0-paper-thursday-three-stage-fill-ladder` übernimmt
deshalb die dreistufige Ausführung in den Bot: Mid minus 0,20 für 5 Minuten,
Mid minus 0,40 für 5 Minuten und Mid minus 0,65 für 5 Minuten, anschließend
Abbruch. Signal, Struktur, VIX-Filter, Positionszahl und Gewinnziel bleiben
unverändert.

Version `1.5.0-paper-weekday-fill-experiment` ändert ausschließlich für den
14.–27.08.2026 die zugelassenen Einstiegstage und die experimentellen
Positionsgrenzen. Die dreistufige Preislogik bleibt unverändert. Nach Ende des
Fensters kehrt die Konfiguration ohne manuellen Eingriff zu Donnerstag und den
normalen Limits zurück.
