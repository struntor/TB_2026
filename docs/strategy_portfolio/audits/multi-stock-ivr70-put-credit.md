# Prüfbericht: Multi-Stock IVR70 Put-Credit

Stand: 23.07.2026

## Ergebnis

Die Bugfix-Version `multi-stock-ivr70-put-credit@1.0.1` ist als zweite separat schaltbare Optionsstrategie in den gemeinsamen Runner integriert. Sie ist ausschließlich für das IBKR-Paperkonto zugelassen und noch nicht produktionsreif.

## Nachgewiesene Eigenschaften

- Kausales Cross-Signal: vorheriger modellierter IVR höchstens 70, letzter abgeschlossener Handelstag über 70.
- Keine Options- oder Kursinformation des Einstiegstags fließt in die Signalerzeugung ein.
- Reale Live-Optionskette entscheidet über konkreten Verfall, exakte 10-Punkte-Strikes, Delta, Credit und Griechen.
- Der Short-Strike muss innerhalb der vorab spezifizierten absoluten Delta-Spanne 0,16–0,22 liegen; ein positiver Credit bei einem weiter entfernten Delta ist kein Ersatz.
- Der Earnings-Veto wird erst gegen den tatsächlich gewählten Verfall plus sieben Tage angewandt.
- Verfall und Earnings werden vor der vollständigen Leg-Quote-Abfrage geprüft; anschließend werden nur tatsächlich qualifizierte Short-/Long-Paare gemeinsam bewertet.
- Entries werden nach definiertem Maximalverlust und danach Liquidität priorisiert.
- SPXW-15DTE und Multi-Stock-IVR70 teilen einen festen offenen USD-Risikodeckel.
- DU-Paperkonto-Sperre ist im Ausführungsadapter und Monitor nicht über den allgemeinen Live-Schalter aufhebbar.
- Journal erfasst Strategy-/Rule-Version, Faktoren, Greeks, BAG-Legs, Orderstatus, Ausführungen und Blocker.

## Evidenz

- Vollständiger Kandidatenpool: 1.787 Trades je Delta-Variante vor Portfoliobegrenzung.
- Gewählter Delta-0,19-Kandidat: 174 Trades, +2.486,69 USD, 73,56 % Gewinner, MaxDD 1.350,73 USD.
- Maximal modelliertes offenes Risiko: 2.498,60 USD bei höchstens drei Positionen.
- CTAS-Referenzfall reproduziert den historischen IVR-Wert bis auf Maschinengenauigkeit.
- Dreizehn Tests prüfen Cross-Regel, frühes Earnings-Veto, DTE-Fehlercode, exakte Leg-Paare, harte Delta-Spanne, konservativen Credit, Orderrichtung/Limit, Paper-Guard, gemeinsamen Risikodeckel, State-Schema, Exit-Fehlerzustand, präzise Journalgründe und die vollständige Dry-Run-Journalstrecke.

## Grenzen

- IVR und historische Optionspreise sind Proxy-Werte; gerade der Credit beeinflusst die Portfoliowahl.
- Das historische Aktienuniversum enthält Survivorship-Bias.
- Earnings beruhen live noch auf einer kausalen SEC-Zyklusprognose, nicht auf einem vollständig verifizierten angekündigten Earnings-Kalender.
- Der 25.000/10-%-Deckel ist im Runner derzeit als 2.500-USD-Modellbudget abgebildet. EUR/USD-Umrechnung ist vor echtem Handel erforderlich.
- Noch kein echter Paper-BAG-Fill und kein Paper-Exit beobachtet.
- Keine Sektorgrenze, weil die derzeitige historische Sektorzuordnung nur etwa 52 % der Kandidaten abdeckt und einen Backtestvergleich verzerren würde.

## Freigabe

Status: `bot_integration`, `frozen_candidate`, `paper_only`, nicht produktionsberechtigt.

Nächster Schritt: an einem regulären Handelstag erst Dry-Run, dann einen beaufsichtigten Paper-Orderlauf ausführen und Quotes, Fill, Gebühren, State und Journal gegen TWS abgleichen.
