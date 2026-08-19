# Produktionsreife der untersuchten Strategien

Stand: 17. August 2026

## Kurzfazit

Unter einem strengen Produktionsbegriff ist noch keine Strategie ohne Auflagen
freigegeben. Der **Multi-Asset Daily Regime / Top50 Core** ist aber technisch
und operativ so weit, dass er nach einer letzten dokumentierten Freigabe als
kleiner, eng begrenzter Live-Pilot betrieben werden kann. Er ist damit der
einzige derzeitige Produktionskandidat.

Zwei Optionsstrategien gehören in ein fortgeschrittenes Paper-Trading:

1. **SPXW 75-DTE Put-BWB**
2. **SPXW 15-DTE Put-Credit Income Core**

Weitere integrierte Strategien müssen zunächst echte Signale, vollständige
Order-Lebenszyklen oder eine korrigierte historische Revalidierung liefern.
Eine Integration in den Bot ist ausdrücklich **keine** Produktionsfreigabe.
Alle optionalen Strategien werden im Runner über eigene `--enable-...`-Schalter
aktiviert; ohne Schalter sind sie nicht automatisch aktiv.

## Bewertungsmaßstab

Eine Strategie gilt erst dann als produktionsreif, wenn alle folgenden Ebenen
ausreichend belegt sind:

1. eingefrorene, versionierte und kausale Signaldefinition;
2. reproduzierbarer Backtest mit unabhängiger oder echter Forward-Evidenz;
3. bei Optionen echte Quotes beziehungsweise konservative ausführbare Preise,
   nicht nur Black-Scholes- oder IV-Proxies;
4. Kosten-, Slippage-, Liquiditäts- und Tail-Risiko-Prüfung;
5. verbindliches Portfolio-Sizing, Risikodeckel und Kill-Switch;
6. vollständiger Paper-Lebenszyklus von Signal über Fill bis Exit;
7. Neustart-, Deduplizierungs- und Journal-Reconciliation-Test;
8. explizite Freigabe der konkreten Strategieversion für echtes Kapital.

Die Bezeichnungen in diesem Bericht bedeuten:

- **Produktionskandidat:** Ein kleiner Live-Pilot ist nach Abschluss einer
  kurzen Freigabe-Checkliste vertretbar.
- **Fortgeschrittenes Paper:** Backtest und Orderpfad sind weit entwickelt,
  aber der vollständige reale Lebenszyklus ist noch nicht ausreichend belegt.
- **Paper/Shadow:** Nur beobachten oder im Paperkonto testen; keine
  Produktionsfreigabe.
- **Research:** Hypothese eingefroren oder geparkt; nicht als Orderstrategie
  aktivieren.
- **Verworfen:** Unter den getesteten Bedingungen kein belastbarer positiver
  Erwartungswert oder unvertretbares Risiko.

## Reifegrad-Matrix

| Strategie | Ergebnis / wichtigste Evidenz | Bot-/Paper-Stand | Urteil | Nächster notwendiger Nachweis |
|---|---|---|---|---|
| Multi-Asset Daily Regime / Top50 Core | Kausaler, reproduzierbarer Kern; aktuelle Baseline positiv; echte Paper-Brackets und abgeschlossene Trades vorhanden | 25 Paper-Trades über zwei Versionen, 46 Execution-Zeilen, 17 Trades mit realisiertem P/L | **Produktionskandidat für begrenzten Live-Pilot** | Aktuelle Sizing-Version gegen Backtest reconciliieren, realistische Gap-/Slippage-Sensitivität dokumentieren, Live-Risikodeckel und Freigabe icfesthalten |
| SPXW 75-DTE Put-BWB | 136/136 historische Strukturen exakt reproduziert; P/L-Korrelation 0,9971; ein isolierter TWS-Paper-Fill zu 4,10 Credit und inverse GTC-TP-Combo angelegt | Im Bot; temporärer Mo-Fr-Fill-Test; integriertes Journal enthält bisher keine Execution für die normalen Versuche | **Fortgeschrittenes Paper** | Mehrere Entry-Fills über die Preisleiter, mindestens ein echter Exit-Fill, Neustart und Journal-Reconciliation; Tail-Risiko weiter separat begrenzen |
| SPXW 15-DTE Put-Credit Income Core | 129 kausale Trades, +10.109,60 USD, 87,6 % Treffer, MaxDD -1.565,20 USD; 129/129 Regelparität | Im Bot; ein Trade und drei Leg-Executions, aber noch kein geschlossener Journal-Lebenszyklus | **Fortgeschrittenes Paper** | Entry bis TP/Damage-Exit/Expiry vollständig beobachten und reconciliieren; mehrere unabhängige Paper-Trades |
| SPX 0DTE Bull-Put + IU30 Butterfly | 09:40-Variante: 193 Signale, 189 handelbar, 18 IU30; Natural-P/L +16.249 USD, PF 3,11, DD -2.423 USD | Im Bot; 8 Paper-Entscheidungen, noch kein Trade oder Fill | **Paper/Shadow** | Erstes echtes Signal mit vollständigem Combo-Fill und Exit; explizite Entscheidung, ob IU30-Risiken oberhalb des 2.500-USD-Deckels erlaubt sind |
| Multi-Stock IVR70 Put-Credit | Proxy-Portfolio: 174 Trades, +2.486,69 USD, 73,56 %, MaxDD -1.350,73 USD; historische IVR und Optionspreise bleiben Proxy | Im Bot; 51 Scanner-Entscheidungen, kein Trade, keine Order, kein Fill | **Paper/Shadow** | Echte Quote-, Delta- und IVR-Stichprobe sowie mehrere vollständige Paper-Lebenszyklen einschließlich 21-DTE-/Roll-Management |
| SPY Failed Auction Hard12 First-Touch Fly | Konservative reale Stichprobe positiv, aber extrem selten: nur 12 unveränderte Signale; 2024-2026 sieben Trades und +477,50 USD | Im Bot, unit-getestet; kein belegter TWS-Fill | **Paper/Shadow** | Seltenes Signal unverändert forward sammeln; erster vollständiger Fill/Exit; keine Aufweichung zur künstlichen Frequenzerhöhung |
| XSP VIX-Pullback 100-DTE, 10 Punkte | Kausal revalidiert: 22 Closed, 19 Gewinner, +490,60 USD; Q-MTM-DD -569,80 USD, Roh-DD -919,60 USD; 2022 negativ | Technischer Paperpfad und DU-Guard vorhanden; Entry-Quote-Gate korrigiert und Tests erfolgreich | **Paper/Shadow mit Paper-Orders** | Vollständige Paper-Lebenszyklen inklusive TP60/60-DTE-Exit und Journal-Reconciliation; keine weitere historische Parameterwahl |
| IWM/RVX Pullback 100-DTE Put-Credit | Zehn echte ThetaData-Trades, alle Gewinner, +580,40 USD; MTM-DD -277,30 USD; zu kurzer und regimearmer Zeitraum | Nicht integriert | **Forward-Scanner / Research** | Unveränderte Signale forward sammeln, insbesondere Stress-/Bear-Markt; danach Paperintegration entscheiden |
| GLD/GVZ Pullback 100-DTE Put-Credit | 14 echte Trades, 13 Gewinner, +596,80 USD, aber ein Verlust -476,60 USD und 2026 negativ | Nicht integriert | **Forward-Scanner / Research** | Mehr unabhängige Signale; Krisenfilter nicht nachträglich optimieren; erst bei stabiler Forward-Evidenz integrieren |
| Multi-Asset VWAP Stretch-Revert Long + Higher-Low | 157 Trades, +12,38 R nach Kosten, PF 1,27, MaxDD -7,60 R; Filter nicht in unangetastetem OOS gewählt | Nicht integriert | **Research, eingefroren** | Definition unverändert forward beobachten; bei stabiler Bestätigung Aktienumsetzung vor Optionsmapping prüfen |
| VWAP Stretch-Revert Long mit Sector-RS und normalem Volumen | 29 echte Forward-Trades, +2,76 R, aber Konfidenzintervall enthält null und die drei besten Trades drehen das Ergebnis | Nicht integriert | **Research, eingefroren** | Mehr Forward-Trades; keine zusätzlichen Markt-/Sektorfilter, da diese die Forward-Leistung verschlechterten |
| SPX Flyagonal 12-15 DTE | Kleine positive Managementvariante, aber nur 54 Fälle; Variante auf denselben Daten gewählt | Nicht integriert | **Research / geparkt** | Nur mit vorab festgelegtem Setup und unabhängigem OOS wieder aufnehmen |
| SPX 45-DTE Put-Credit Delta 30, Breite 20 | Historischer Kandidat ohne ausreichende heutige Produktions- und Execution-Evidenz | Nicht integriert | **Research / geparkt** | Nur als neue präregistrierte Untersuchung wieder aufnehmen |
| Multi-Asset EMA Options / OI Context | Kontextforschung, kein ausreichend bestätigter eigenständiger Handelsedge | Nicht integriert | **Research / geparkt** | Neue Hypothese mit klarer OOS-Trennung erforderlich |
| SPY Failed Auction Hard6 und alter 2-DTE-BWB | Kontroll-/Vorgängervarianten; Hard6 schwächer, 2-DTE-Pfad nicht die heutige freigegebene Hypothese | Nicht integriert | **Research / geparkt** | Nicht parallel optimieren; Hard12 bleibt die einzige Forward-Hypothese |
| SPY Volume/VIX 0DTE Call Fly | OOS-Ergebnis nicht robust; weitere Filterwahl wäre Datenanpassung | Nicht integriert | **Geparkt** | Nur mit fachlich neuer, vorab definierter Struktur und neuem Forward-Fenster |

## Verworfen oder unter den getesteten Bedingungen nicht handelbar

| Strategie-Familie | Ergebnis | Entscheidung |
|---|---|---|
| Jade Lizard | Echte ThetaData-Auswertung: nur 59 verifizierte/tradable Fälle aus 340 Signalen, -4.370,85 USD, PF 0,467; Loss-Management-Varianten ebenfalls negativ | **Verworfen** |
| SPY Front Ratio Spread | Reale Stichprobe nur vier unabhängige Trades je Seite; Call nicht stabil, Put-Stichprobe zu klein; zusätzlicher ungedeckter Short erzeugt großes beziehungsweise unbegrenztes Tail-Risiko | **Verworfen / nicht papern** |
| Value-Acceptance Iron Condor, 7/2 DTE | Underlying-Aufenthaltstendenz vorhanden, aber reale Credits deutlich niedriger als Proxy; 2025/2026 negativ beziehungsweise 2-DTE klar negativ | **Verworfen** |
| Acceptance-to-Expansion Debit Spread | 760 Signale; alle sechs primären Optionskonfigurationen negativ, Edge verschlechtert sich 2026 | **Verworfen** |
| Failed Value-Area Breakout / POC Re-entry Debit Spread | 518 Next-Open-Ausführungen; POC wird zwar oft berührt, aber terminale Rendite und alle primären Spreadvarianten sind negativ | **Verworfen** |
| Overnight-Inventory-Correction Fly | 22 CRV-10-Trades; Expiry-Profil zwar +674 USD, aber nur drei Gewinner; Body-Touch-Exit -1.139 USD, alle Partitionen negativ | **Verworfen / geparkt** |
| Implied-Density Fly | Entwicklung 2025 positiv, Validation 2026 -3.707 USD und Holdout Mai 2026 -1.280 USD | **Verworfen** |
| Täglicher 0DTE Trend-Fly | 598 echte Quote-Strukturen, konservativ -12.796 USD, 16,2 % Treffer | **Verworfen** |
| Täglicher 0DTE Batman | 531 Modelltage, Ergebnis von wenigen Extremgewinnern abhängig; reale 09:40-Quote-Rekonstruktion -18.966 USD, Bootstrap enthält null | **Verworfen** |
| Sector-RS Call-Debit-Spread | Forward bei realistischer IV und Slippage negativ; Zeitwert und Kosten vernichten den kleinen Underlying-Edge | **Verworfen** |
| Sector-RS Put-Credit-Spread | Forward risikogewichtet -0,38 R; schon 2 Cent Slippage machen alle IV-Szenarien negativ | **Verworfen** |
| SPX 15-DTE Damage Bear-Call / Put-Debit | Vorhandene Gegenstrategien lieferten keinen eigenständig robusten Edge | **Verworfen** |
| Multi-Asset Post-Break Range-Mid Long | Kein stabiler positiver Erwartungswert | **Verworfen** |
| USO/OVX Pullback Put-Credit | Rohstoff-Volatilitätsumsetzung unter den geprüften Bedingungen nicht robust | **Verworfen** |

## Journal-Evidenz

Die folgenden Werte stammen aus der aktuellen Journal-Datenbank. Backtest-Zahlen
sind wegen wiederholter Baseline-Läufe keine Anzahl unabhängiger Trades. Für die
Reifeprüfung sind vor allem Paper-Orders, Executions und geschlossene
Lebenszyklen relevant.

| Strategieversion | Paper-Entscheidungen | Trade-Datensätze | Orders | Executions | Geschlossen mit P/L |
|---|---:|---:|---:|---:|---:|
| Manual Regime Ruleset v1 | 518 | 22 | 66 | 42 | 16 |
| Manual Regime Cluster-/Sizing-Version | 300 | 3 | 9 | 4 | 1 |
| SPXW 15-DTE v1.1.0 | 14 | 1 | 1 | 3 | 0 |
| Multi-Stock IVR70 v1.0.x | 51 | 0 | 0 | 0 | 0 |
| SPX 0DTE IU30 v1.0.0 | 8 | 0 | 0 | 0 | 0 |
| SPXW 75-DTE integrierte Varianten | 8 | 3 | 3 | 0 | 0 |

Beim SPXW-75-DTE-BWB liegt zusätzlich ein separat verifizierter TWS-Paper-Fill
vom 13. August 2026 vor. Dass dieser nicht als Execution im normalen
Strategie-Journal erscheint, ist selbst ein noch zu schließender
Reconciliation-Punkt.

Die Journalübersicht kann jederzeit reproduziert werden mit:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
python .\scripts\summarize_strategy_journal_evidence.py
```

## Empfohlene Arbeitsreihenfolge

### 1. Manual Regime Core kontrolliert freigeben

Vor einem echten Live-Pilot müssen nur noch diese konkreten Punkte mit der
eingefrorenen Version abgehakt werden:

- Backtest-Entscheidung, Paper-Entscheidung und tatsächlich ausgeführter Trade
  für die neue Cluster-/Sizing-Version über dieselben Handelstage vergleichen;
- Gap-through-Stop und konservative Slippage als Sensitivität ausweisen;
- maximales Tages-, Strategie- und Portfoliorisiko sowie Kill-Switch schriftlich
  festlegen;
- zunächst kleinste Positionsgröße und eng begrenztes Gesamtrisiko;
- konkrete Version und Startzeitpunkt im Journal als Produktionsfreigabe
  markieren.

### 2. SPXW 75-DTE und SPXW 15-DTE im Paperkonto vervollständigen

Hier geht es nicht um weitere Parameteroptimierung, sondern um reale
Ausführung:

- Entry-Combo-Fill;
- TP-/Schaden-/Expiry-Exit;
- Neustart bei offener Position;
- keine Doppelorder;
- vollständige TWS-zu-Journal-Reconciliation.

Mehrere normale Lebenszyklen sind wertvoller als ein weiterer In-Sample-Sweep.

### 3. Seltene Kandidaten nur beobachten

SPX 0DTE IU30, IVR70 und Failed Auction Hard12 bleiben unverändert als
Paper-/Shadow-Kandidaten aktivierbar. Ihre geringe Frequenz darf nicht durch
nachträgliches Aufweichen der Filter erkauft werden.

### 4. XSP zuerst historisch korrigieren

Die korrigierte Zeitlogik muss den vollständigen exakten Backtest neu
durchlaufen. Bis dahin sollte der Schalter zwar technische Entscheidungen
journalisieren dürfen, aber keine Order senden.

### 5. Research-Backlog klein halten

IWM/RVX, GLD/GVZ und die beiden eingefrorenen VWAP-/Sector-RS-Kandidaten werden
forward beobachtet. Die verworfenen Fly-, Condor-, Jade-Lizard-, Ratio- und
Debit-Spread-Familien werden nicht weiter parametrisch optimiert. Eine
Wiederaufnahme braucht eine fachlich neue Hypothese und ein neues, unangetastetes
Validierungsfenster.

## Gesamtentscheidung

- **Kontrollierter Produktionspilot vertretbar:** Multi-Asset Daily Regime /
  Top50 Core, nach dokumentierter Abschlussfreigabe.
- **Weiteres echtes Paper-Trading:** SPXW 75-DTE Put-BWB und SPXW 15-DTE
  Put-Credit Income Core.
- **Paper/Shadow ohne Produktionsfreigabe:** SPX 0DTE IU30, Multi-Stock IVR70,
  Failed Auction Hard12; XSP erst nach korrigierter Revalidierung.
- **Nur Forward-Research:** IWM/RVX, GLD/GVZ, VWAP/RS-Kandidaten.
- **Geparkt oder verworfen:** alle übrigen in den Tabellen genannten Familien.
