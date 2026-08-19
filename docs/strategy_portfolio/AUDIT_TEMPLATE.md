# Pruefbericht: <Strategie>

- Strategy ID: `<id>`
- Audit-Datum: `YYYY-MM-DD`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `documented_only | artifact_verified | partially_reproduced | reproduced`
- Empfohlener Reifegrad: `<lifecycle_stage>`
- Empfohlener Arbeitszustand: `<work_state>`

## 1. Urteil in Kurzform

Was ist belastbar, was ist nur dokumentiert und was ist derzeit nicht belegbar?

## 2. Gepruefte Quellen

### Code

- Pfad, relevante Funktionen/Konstanten und deren tatsaechliche Rolle.

### Ergebnisartefakte

- Pfad, Zeitraum, Zeilen/Trades und zentrale Spalten.

### Dokumentation

- Pfad und Stand.

## 3. Tatsaechlich implementierte Strategie

- Universum/Instrument
- Entscheidungszeit
- Entry
- Exit und Management
- Kosten-/Fill-Modell
- Daten- und Zustandsabhaengigkeiten

Abweichungen zwischen Beschreibung und Code werden hier explizit genannt.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |

Evidenzstatus:

- `documented`: aus Bericht uebernommen, nicht neu berechnet.
- `artifact_verified`: gegen vorhandene CSV/JSON/Trades geprueft.
- `partially_reproduced`: mit vorhandenem Code teilweise neu berechnet.
- `reproduced`: vollstaendig aus definierten Inputs neu erzeugt.
- `conflict`: Quellen widersprechen sich.
- `unknown`: nicht belastbar feststellbar.

## 5. Methodische Risiken

- Look-ahead/Data Leakage
- In-Sample/OOS/Testperiodenverbrauch
- Multiple Testing/Parameterselektion
- Survivorship/Universe Selection
- Kosten, Slippage und Liquiditaet
- Missing Data/Quote Policy
- Stichprobe und Regimekonzentration
- Pfadabhaengiger State/Reproduzierbarkeit

## 6. Money Management und Portfolio

Was ist getestet, was ist nur Strategie-Exit-Management und was fehlt auf Portfolioebene?

## 7. IBKR-Integrationsreife

- Trade-Intent-Vertrag
- Dry-Run/Replay
- TWS Paper
- Order-Gate
- Restart/Duplikatschutz
- Produktionsfreigabe

Die Faehigkeit, eine Order an TWS zu senden, ist allein keine Produktionsreife.

## 8. Widersprueche und offene Punkte

Keine stillschweigende Aufloesung: konkurrierende Baselines, Zeitraeume oder Kennzahlen einzeln nennen.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe:
- Empfohlene Stufe:
- Begruendung:
- Produktionsberechtigt: `false`, sofern nicht explizit anders vom Nutzer freigegeben und technisch belegt.

## 10. Naechster minimaler Test

Genau ein Test, der die groesste aktuelle Unsicherheit reduziert. Keine neue Parametersuche ohne vorab definiertes Entscheidungskriterium.
