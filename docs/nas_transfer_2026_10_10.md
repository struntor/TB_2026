# Geprüfter NAS-Umzugsstand vom 10.10.2026

Abgeschlossen um 15:03:24 CEST. Alle 804 Nutzdateien (14.487.634.603 Bytes)
wurden nach dem Kopieren per SHA-256 gegen ihre Quelle geprüft.

| Inhalt | NAS-Ziel | Umfang |
| --- | --- | --- |
| Kursdaten | `\\Synology_NAS\home\Rohdaten` | 561 Rohdaten-CSV-Dateien |
| Downloader-Auswahl | `\\Synology_NAS\home\Rohdaten\assets.csv` | 1 Datei |
| IVR70-Auswahl und Earnings-Caches | `\\Synology_NAS\home\Rohdaten\runtime_inputs` | 233 Dateien |
| Aktuelles Journal | `\\Synology_NAS\home\Journal\trading_journal.sqlite` | 10.490.953.728 Bytes |
| Strategie-Zustände | `\\Synology_NAS\home\Journal\strategy_state` | 8 JSON-Dateien |

Das Journal wurde um 14:44:47 CEST als konsistenter SQLite-Snapshot erstellt.
`PRAGMA quick_check` lieferte `ok`. SHA-256 der Journal-Sicherung:
`171d902deca222c576ed25c27328761a44aa8398f74bf14fb4b620939c98c989`.
Bei der Prozessprüfung während des Exports lief kein Trading-Bot oder
Rohdaten-Downloader.

Die vollständigen Dateimanifeste liegen im NAS-Ordner `Journal`:

- `transfer_manifest_20261010T124601Z.json`
- `runtime_inputs_manifest_20261010T124758Z.json`

Vorherige abweichende NAS-Rohdaten sind mit dem Zusatz `.before-20261010T124601Z`
erhalten. Lokale Originale und die lokale Snapshot-Datei unter
`D:\Trading\tmp\nas_migration_20261010_01` wurden ebenfalls erhalten.
Die aktive lokale Bot-Konfiguration wurde nicht auf die NAS-Datenbank umgestellt.

Wiederherstellung und Installation:
[Neuer Rechner mit NAS-Daten](new_computer_nas_setup.md).
Code und Installationsdateien werden gemeinsam mit diesem Übertragungsbericht
im Repository `TB_2026` versioniert. Die großen Datenbestände liegen auf dem NAS.
