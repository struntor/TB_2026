# Pruefbericht: USO OVX-Pullback 100DTE Put-Credit

- Strategy ID: `uso-ovx-pullback-100dte-put-credit`
- Audit-Datum: `2026-08-11`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `statistical_evidence`
- Empfohlener Arbeitszustand: `rejected`

## 1. Urteil in Kurzform

Die direkte Uebertragung des Volatilitaets-Pullbacks auf OVX/USO wird verworfen.
Basiswertpfad, Optionsproxy und echte Ketten liefern zusammen keine handelbare
historische Evidenz.

## 2. Gepruefte Quellen

- `research/test_commodity_vol_pullback_100dte_proxy.py`
- `research/test_commodity_vol_pullback_crisis_guards_proxy.py`
- `research/verify_commodity_vol_pullback_entry_quotes_thetadata.py --pair OVX_USO`
- `D:/Trading/Rohdaten/Rohdaten_OVX.csv`
- `D:/Trading/Rohdaten/Rohdaten_USO.csv`

## 3. Tatsaechlich getestete Strategie

Dieselben q60/Drop4-/Momentum-/SMA200-Regeln, 100-DTE-Untergrenze, Delta -0,20,
zehn Punkte Breite, TP60 und 50-DTE-Zeitexit wie bei IWM und GLD. Zusaetzlich
wurde das vorherige rollierende OVX-q90 als Krisenobergrenze geprueft.

## 4. Ergebnispruefung

Ohne q90-Gate entstehen 17 Basissignale. Nach 60 Sitzungen ist USO nur in
56,25 % der auswertbaren Faelle nichtnegativ, obwohl OVX in 68,75 % gefallen ist.
Der gemeinsame Proxy bleibt negativ. Nach q90 und SMA200 verbleiben sieben
Signale; nur das Ereignis 2026-08-05 besitzt ein vollstaendiges zehn Punkte
breites ThetaData-Paar innerhalb der Delta-Toleranz. Sechs Ereignisse sind nicht
als gemeinsam quotierte Struktur reproduzierbar.

## 5. Methodische Risiken

- USO besitzt Roll- und Reverse-Split-Effekte und ist kein einfacher Spot-Oel-
  Ersatz.
- Hohe OVX und ausgepraegter Put-Skew machen zehn Punkte Breite relativ eng.
- Ein einziger exakter Einstieg erlaubt keinen Exit- oder Erwartungswerttest.

## 6. Money Management und Portfolio

Nicht anwendbar, weil keine belastbare handelbare Stichprobe existiert.

## 7. IBKR-Integrationsreife

Vor Integration verworfen. Kein Scanner, Adapter oder Orderpfad vorgesehen.

## 8. Widersprueche und offene Punkte

OVX normalisiert sich haeufiger als der USO-Kurs stabil bleibt. Damit wird die
Volatilitaetsthese nicht sauber in einen sicheren Short-Put-Pfad uebersetzt.
Eine breitere oder andere Oelstruktur waere eine neue Hypothese und darf dieses
negative Ergebnis nicht nachtraeglich umdefinieren.

## 9. Reifegradentscheidung

- Stufe: `statistical_evidence`
- Arbeitszustand: `rejected`
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Kein weiterer Test dieser Struktur. Nur eine fachlich neue Oelstrategie mit
vorab festgelegtem Underlying, Laufzeit- und Breitenmodell darf als neue
Strategy-ID wieder aufgenommen werden.
