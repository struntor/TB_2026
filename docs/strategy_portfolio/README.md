# Strategie-Portfolio

Dieses Verzeichnis ist die zentrale, dauerhafte Uebersicht aller Strategien, Varianten und kurzfristigen Ideen mit dem gemeinsamen Ziel einer moeglichen Integration in den `IBKR_TradingBot`.

Die Registry ist zunaechst rein beschreibend. Sie wird von keinem Live-Runner importiert und kann keine Orderfreigabe veraendern.

## Grundmodell

Jede Strategie hat drei voneinander unabhaengige Einordnungen:

1. `lifecycle_stage` beschreibt den erreichten fachlichen Reifegrad.
2. `work_state` beschreibt, ob gerade daran gearbeitet wird.
3. `priority` beschreibt, wie die Strategie gegenueber anderen Arbeiten eingeordnet ist.

Dadurch verliert eine fast fertige, aber geparkte Strategie ihren Reifegrad nicht, wenn zwischendurch eine neue Idee als Kurztest untersucht wird.

## Lebenszyklus

| Stufe | Bedeutung | Mindestanforderung fuer den Eintritt |
| --- | --- | --- |
| `idea` | Noch ungetestete Hypothese | Hypothese, Instrument, Zeithorizont und erwarteter Mechanismus sind notiert. |
| `exploration` | Erste deskriptive/statistische Untersuchung | Datenquelle und ein reproduzierbarer Untersuchungsweg sind benannt. |
| `statistical_evidence` | Erste messbare Evidenz | Resultate, Stichprobe, Zeitraum und bekannte Einschraenkungen sind gespeichert. |
| `backtest_validation` | Deterministischer Backtest vorhanden | Strategie-Spezifikation, Kostenannahmen, Trades und Baseline sind reproduzierbar. |
| `robustness_validation` | Stabilitaet wird geprueft | OOS-/Walk-forward-Sicht, Sensitivitaet und Look-ahead-Audit sind dokumentiert. |
| `money_management` | Positions- und Portfoliorisiko wird entwickelt | Edge-Baseline ist eingefroren; Sizing, Exposure, Drawdown und Kill-Switch werden separat getestet. |
| `bot_integration` | Strategie wird an den Bot-Vertrag angebunden | Normalisierte Trade-Intents, Replay/Dry-Run und Fehlerverhalten sind definiert. |
| `paper_trading` | Vollstaendiger Ablauf ohne autonome Produktion | TWS-Paper/Dry-Run, Restart, Duplikatschutz, Logging und Betriebskontrollen sind geprueft. |
| `production` | Explizit freigegebene Live-Strategie | Schriftliche Freigabe, unveraenderliche Release-Referenzen und Rollback-/Kill-Switch-Plan liegen vor. |

Eine hoehere Stufe ist kein automatisches Qualitaetsurteil. Eine Strategie kann jederzeit auf einer Stufe gestoppt, geparkt oder verworfen werden.

## Arbeitszustand

- `active`: aktuell eingeplante Arbeit.
- `frozen_candidate`: fachliche Baseline ist eingefroren; nur Validierung oder Integration darf sie noch veraendern.
- `parked`: bewusst pausiert; Reifegrad und naechster Schritt bleiben erhalten.
- `blocked`: ein konkretes Hindernis verhindert sinnvolle Fortsetzung.
- `monitoring`: keine Entwicklung; neue Daten oder Paper-Ergebnisse werden gesammelt.
- `rejected`: fachlich verworfen, negative Evidenz bleibt dokumentiert.
- `retired`: ehemals weiter entwickelte oder produktive Strategie wurde stillgelegt.

## Prioritaet und Arbeitsstrang

- `primary`: aktuell wichtigster Produktionspfad.
- `secondary`: geplanter Nebenpfad.
- `quick_probe`: bewusst begrenzter Kurztest einer neuen Idee.
- `backlog`: erfasst, aber derzeit nicht eingeplant.

Varianten und Overlays verweisen mit `parent_strategy_id` auf ihre Basisstrategie. Ein neuer Filter wird nicht automatisch zu einer neuen Hauptstrategie.

## Produktionsschutz

- Neue Registry-Eintraege starten immer mit `production_eligible: false`.
- Registry und Portfolio-Uebersicht werden nicht von Live-Code importiert.
- `production_eligible: true` ist nur in der Stufe `production` erlaubt.
- Die Produktionsfreigabe benoetigt einen konkreten Runner, Order-Gate, Paper-Nachweis und eine explizite Entscheidung des Nutzers.
- Forschung, Strategiecode, Money Management und Broker-Ausfuehrung bleiben getrennt.
- Ein guter Backtest allein ist keine Produktionsfreigabe.

## Dateien

- `registry.json`: maschinenlesbare Quelle fuer Strategie- und Statusdaten.
- `PORTFOLIO.md`: kompakte, aus der Registry erzeugte Uebersicht.
- `STRATEGY_STATUS_TEMPLATE.md`: Vorlage fuer einen ausfuehrlichen Strategie-Handoff.
- `../../scripts/strategy_registry.py`: validiert die Registry und erzeugt `PORTFOLIO.md` neu.

## Arbeitsablauf

Neue Idee erfassen:

1. Stabilen `strategy_id` vergeben.
2. Mit `lifecycle_stage: idea`, `production_eligible: false` und einer klaren Hypothese eintragen.
3. Bei einem Kurztest `priority: quick_probe` sowie eine begrenzte Fragestellung verwenden.
4. Ergebnis und Entscheidung auch dann speichern, wenn die Idee verworfen wird.

Status aktualisieren:

1. Neue Evidenz verlinken.
2. `current_summary`, `next_action` und `last_updated` aktualisieren.
3. Reifegrad nur anheben, wenn das dokumentierte Eintrittskriterium erfuellt ist.
4. Registry validieren und Portfolio neu rendern:

```powershell
python .\scripts\strategy_registry.py validate
python .\scripts\strategy_registry.py render
```

