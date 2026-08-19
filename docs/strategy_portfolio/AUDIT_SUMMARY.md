# Uebergreifender Strategie-Audit

Stand: 2026-07-14

## Zweck und Evidenzgrenze

Dieser Audit rekonstruiert den tatsaechlichen Stand aus vorhandenem Code, Konfigurationen, Trade-/Result-Artefakten und Handoffs. Vorhandene Kennzahlen werden nur dann als reproduziert bezeichnet, wenn sie aus definierten Inputs neu erzeugt wurden. Eine Uebereinstimmung zwischen Bericht und gespeicherter CSV ist `artifact_verified`, aber noch kein unabhaengiger Re-Run des Backtests.

Die Einzelberichte liegen unter `docs/strategy_portfolio/audits/`.

## Querschnittsurteil

### 1. Produktion ist derzeit fuer keine Registry-Strategie belegt

Der Multi-Asset-Regime-Core kann Signale berechnen und Test/Paper-Orders an TWS uebergeben. Das belegt die technische Orderroute, nicht die vollstaendige Produktionsreife. Fuer die anderen Strategien ist noch kein gleichwertiger IBKR-Adapterpfad dokumentiert.

### 2. Zwei Money-Management-Pfade existieren

Der operative Manual-Regime-Runner berechnet die Stueckzahl intern aus `risk_per_trade`, Size-Multiplikator und Stop-Distanz. Der modulare `IBKR_TradingBot` besitzt separat einen `MoneyManagementBlock`. Solange nicht entschieden und verifiziert ist, welcher Pfad verbindlich ist, darf aus einem erfolgreichen Dry-/Paper-Test keine allgemeine Money-Management-Freigabe abgeleitet werden.

### 3. Live/Replay-Paritaet ist Zielarchitektur, noch kein vollstaendiger Nachweis

`docs/live_replay_architecture.md` beschreibt persistente Engine-Zustaende und einen gemeinsamen Live-/Replay-Ablauf. Die vorgeschlagenen Replay-/State-Store-Module sind noch nicht vollstaendig implementiert. Der vorhandene `EngineCore` haelt nur begrenzten In-Memory-State.

### 4. Viele Forschungsperioden sind bereits analytisch verbraucht

Mehrere Strategien wurden wiederholt auf denselben Jahren, Exporten oder Problemmonaten analysiert. Diese Perioden sind nicht mehr als unangetastetes OOS zu behandeln, auch wenn ein einzelnes Skript sie als OOS bezeichnet. Jeder Bericht nennt deshalb Periodenwiederverwendung und Multiple-Testing-Risiken separat.

### 5. Exit-Management ist nicht gleich Portfolio-Money-Management

Damage-Exit, Thin-Rally-Stop, Profit-Target oder Struktur-Exit koennen Tail-Risiko innerhalb eines Trades steuern. Das ersetzt keine Pruefung von Kapitalbindung, gleichzeitigen Positionen, Korrelationsclustern, Exposure, Tagesverlustgrenzen und Kill-Switches.

### 6. Gespeicherte negative Ergebnisse sind Teil der Evidenz

Verworfene Range-Mid-, Bear-Call- und Put-Debit-Unterzweige bleiben dokumentiert. Sie duerfen nur bei einer fachlich neuen, vorab begrenzten Hypothese erneut geoeffnet werden; eine neue Parameterbezeichnung allein ist keine neue Hypothese.

## Gemeinsamer Produktions-Gate

Eine Strategie kann erst als produktionsberechtigt gelten, wenn mindestens belegt sind:

1. eingefrorene Strategie- und Konfigurationsversion,
2. kausaler und reproduzierbarer Backtest,
3. klar getrennte, nicht fuer die Auswahl verwendete Validierung,
4. realistische Kosten-, Slippage- und Missing-Quote-Policy,
5. Portfolio-Money-Management und Kill-Switch,
6. normalisierter Trade-Intent-Vertrag,
7. Live-/Replay- oder gleichwertiger Regressionstest,
8. TWS-Paper-Test einschliesslich Restart und Duplikatschutz,
9. dokumentierter Order-Gate und Rollback,
10. explizite Freigabe durch den Nutzer.

## Ergebnisuebersicht

| Strategie | Evidenz | Empfohlene Stufe | Arbeit | Kernurteil |
| --- | --- | --- | --- | --- |
| Multi-Asset Daily Regime / Top50 Core | `artifact_verified` | `bot_integration` | `frozen_candidate` | Research und operativer TWS-Pfad sind weit fortgeschritten; dynamischer Closed-Trade-State, Live/Replay-Paritaet und laengerer Paper-Nachweis fehlen. |
| SPX 15DTE Put-Credit Income Core | `artifact_verified` | `robustness_validation` | `active` | 119 Trades und +11.545 USD sind als Artefakt belegt; Filter und Management wurden jedoch auf wiederholt betrachteten Jahren gewaehlt. |
| SPX Flyagonal 12/15DTE | `artifact_verified` | `backtest_validation` | `active` | Direkte Monday-Replays sind negativ; der positive Management-Kandidat wurde aus 16 Varianten auf derselben 54-Trade-Stichprobe ausgewaehlt. |
| SPX 0DTE Bull-Put plus IU30 Butterfly | `artifact_verified` | `backtest_validation` | `active` | +18.966 USD sind in zwei Logs belegt; Kandidat und Baseline entstanden nach umfangreicher Suche auf derselben Population. |
| SPY Failed-Auction zu POC 2DTE BWB | `artifact_verified` | `backtest_validation` | `active` | 14/16 POC-Touches sind belegt; das lokale PnL-Artefakt fuer die 16 Option-Trades fehlt. |
| SPY Volume/VIX 0DTE Call-Fly | `artifact_verified` | `exploration` | `active` | 868 Sessions und 2.604 Leg-Zeilen existieren, aber noch kein Trade-PnL; VIX wird im ausgewerteten Pfad nicht verwendet. |
| Multi-Asset VWAP Stretch-Revert Long | `artifact_verified` | `backtest_validation` | `active` | 197 Trades und +21,63R nach 5 bps je Seite sind belegt; RS60-/Volumenfilter wurden auf derselben 2025/26-Periode ausgewaehlt. |
| SPX 45DTE Put-Credit Delta30 Width20 | `artifact_verified` | `robustness_validation` | `parked` | Zwischen 2024 und 2025H1 ist keine stabil dominante Basis belegt; VP-Management verbessert nicht robust. |
| Multi-Asset EMA plus Options/OI | `artifact_verified` | `statistical_evidence` | `parked` | Options-/OI-Effekte sind assetabhaengig; Universen und OOS-Trennung sind nicht konsistent genug fuer einen Regelstack. |
| VIX-Reversal 100DTE Put-Credit | `artifact_verified` | `idea` | `parked` | Sehr kleine Stichprobe; ein Filter auf positiven konservativen Entry-Credit entfernt mindestens einen negativen Trade aus der Auswertung. |
| Post-Break Range-Mid Long | `documented_only` | `statistical_evidence` | `rejected` | Der negative Grundbefund bleibt, aber 110 Trades/-2.215,55 USD sind keinem vorhandenen Artefakt eindeutig zuordenbar. |
| 15DTE Damage Bear-Call | `artifact_verified` | `statistical_evidence` | `rejected` | Negativer Alternativzweig; keine Wiederaufnahme ohne neue Hypothese. |
| 15DTE Damage Put-Debit | `artifact_verified` | `statistical_evidence` | `rejected` | Varianten untersucht und verworfen; engerer Hedge wurde nicht unabhaengig validiert. |

## Wichtigste Korrekturen gegen den bisherigen Stand

- Der Manual-Regime-Core bleibt auf `bot_integration`: Der korrekte operative Runner bildet Korb, 548-Tage-Historie und aktive Vetos ab. Der generische `live_trade_probe.py` ist nicht der Baseline-Runner.
- `money_management` war fuer 15DTE Core und Flyagonal zu hoch angesetzt, weil unabhaengige Validierung beziehungsweise eine echte Train/Test-Trennung fehlt.
- Der 0DTE-IU30-Kandidat und VWAP-Long werden auf `backtest_validation` gefuehrt, da ihre finalen Filter auf bereits betrachteten Perioden ausgewaehlt wurden.
- Die Range-Mid-Zahlen sind widerspruechlich; dieser Bericht ist deshalb der einzige mit `documented_only` statt `artifact_verified`.
- Keine Strategie ist derzeit `production_eligible`.
