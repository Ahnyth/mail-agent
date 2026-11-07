# Architektur

## Überblick

```
             ┌────────────────────┐
             │ Quelle (nur lesen) │  sources/eml_folder.py · sources/imap.py
             └─────────┬──────────┘
                       │ RawMail (Bytes + Herkunft)
                       ▼
             ┌────────────────────┐
             │ parsing.py         │  RFC 5322 → MailMessage (Text, Anhänge, Schlüssel)
             └─────────┬──────────┘
                       ▼
             ┌────────────────────┐
             │ analysis/          │  claude.py (Claude API)  ──Fehler──▶  rules.py (Stichworte)
             └─────────┬──────────┘  fallback.py verbindet beide
                       │ Analysis (Kategorie, Dringlichkeit, Entwurf, Dokumenttypen, Warnungen)
             ┌─────────┴──────────┐
             ▼                    ▼
  ┌────────────────────┐ ┌────────────────────┐
  │ filing.py          │ │ storage/           │
  │ Dokumentablage     │ │ SQLite             │
  └────────────────────┘ └─────────┬──────────┘
                                   ▼
                       ┌────────────────────┐
                       │ web/ (FastAPI)     │  Übersicht · Detail · Entwurf · Dokumente
                       └────────────────────┘
```

`service/` orchestriert den Ablauf: `MailAgentService` ist der einzige Einstieg (`import_new`, `reanalyze`,
`reanalyze_pending`, `run`) und erzeugt pro Aufruf kleine Helfer (`MailImporter`, `Reanalyzer`, `SentFolderSync`,
`HandoverSync`, `SenderBook`, `CaseExporter`, `JobExecution`); `setup.py` und `factory.py` bauen die Teile aus der
Konfiguration zusammen. `jobs.py` führt Abruf und KI-Nachholen im Hintergrund
aus (Fortschritt für die Weboberfläche) und startet den Abruf zu den Uhrzeiten aus `[fetch]`.
`cli.py` bietet `testdaten`, `einlesen`, `ki-nachholen` und `web`.

**Abruf:** `MailSource.plan()` sucht die neuen Nachrichten (IMAP: `UID SEARCH UID n:* SINCE
<Startdatum>` nach der gespeicherten Position, gültig nur mit derselben `UIDVALIDITY`), `load()`
liest sie in Portionen von 20 auf jeweils eigener Verbindung. Nach jeder Portion wird die Position
gespeichert; ein abgebrochener Abruf macht beim nächsten Mal dort weiter. Bereits gespeicherte Mails
erkennt der Agent zusätzlich an der Message-ID.

## Module

| Modul | Verantwortung |
|---|---|
| `models.py` | Domänenmodell: `MailMessage`, `Analysis`, `Category`, `Urgency`, `Status`, `FiledDocument` |
| `config.py` | TOML-Konfiguration, strenge Validierung, Datenschutzsperre für IMAP + Cloud-KI |
| `parsing.py` | Mail parsen, HTML → Text, Kopfzeilen einzeilig, stabiler Schlüssel (Message-ID-Hash) |
| `contact_form.py` | Mails des Website-Kontaktformulars: Besucher als Absender, nur Formularinhalt |
| `sent.py` | Gesendet-Ordner: Antworten über `In-Reply-To`/`References` oder Empfänger + Betreff zuordnen |
| `handover.py` | Übergabe-Datei zwischen den PCs: packen, verschlüsseln, übernehmen, Vermerk „wer ruft ab“ |
| `background.py`, `processes.py` | Start ohne Fenster (Autostart, `daten/agent.log`); Hilfsprogramme ohne Konsolenfenster |
| `tray.py` | Symbol neben der Uhr (pystray): Klick öffnet, Rechtsklick „Beenden“, Rückfrage nur bei laufendem Abruf |
| `sources/` | `MailSource`-Protokoll (`plan`/`load`); `.eml`-Ordner; IMAP mit Befehls-Allowlist und UID-Position |
| `jobs.py` | ein Hintergrundauftrag zur Zeit (Abruf, KI nachholen) mit Fortschritt; Zeitplan mit Nachholen verpasster Abrufe, schreibt die Übergabe-Datei, wenn sie fällig ist |
| `analysis/` | `Analyzer`-Protokoll; `routing.py` (erst Regeln, dann KI); `rules.py`; `signals.py` (feste Signale: Absenderstatus, IBAN, Fristen, Weiterleitung, Betrugsmerkmale); `claude.py`/`claude_code.py` (zwei Stufen: Einordnen, Entwurf); `fallback.py` |
| `senders.py` | bekannte Absender und Domains aus `config/bekannte_absender.yaml` |
| `eval/` | Testsammlung laden (`tests/eval/faelle`), auswerten, Korrekturen anonymisiert als Fall exportieren |
| `attachments.py` | Anhangtext lesen: PDF (pdfium, mit Sperre, da nicht threadsicher), Scans/Bilder per Tesseract-Prozess |
| `briefing.py` | Morgen-Briefing, Fristen-, Rechnungs- und Wiedervorlage-Listen aus der Datenbank (ohne KI) |
| `exports.py` | `.ics`-Kalenderdatei für Fristen, CSV für Rechnungen (Formel-Schutz) |
| `applications.py` | Checkliste fehlender Bewerbungsunterlagen, feste Eingangsbestätigung |
| `voice.py` | Sprachausgabe mit der deutschen Windows-Stimme (offline per PowerShell, WAV im Speicher) |
| `speech_input.py` | lokale Spracherkennung mit faster-whisper (WAV im Speicher dekodieren, ohne PyAV) |
| `commands.py` | Sprachbefehle per festen Regeln; „Antworte: …“ → Entwurf |
| `filing.py` | Anhänge sicher benennen, ablegen, Herkunft dokumentieren |
| `storage/` | SQLite: `MailRepository` bündelt je Bereich eine kleine Klasse: `messages` (anlegen, finden, laden), `inbox` (Liste und Zähler), `workflow` (offen/erledigt, beantwortet), `analyses` (neue Auswertung, Entwurf), `corrections`, `documents`, `history` (frühere Mails eines Absenders), `positions` (Lesestand je Ordner), `runs` (Laufprotokoll), `state`; dazu `schema.py`, `records.py` (Datenklassen), `conversion.py` |
| `web/` | Weboberfläche: `app.py` setzt nur zusammen; `security.py` (Host- und Herkunftsprüfung, CSP), `change_tracking.py` (Änderungen für die Übergabe), `context.py` (gemeinsamer Zustand der Routen), `forms.py` (Adress- und Formularfelder als Klassen), `views.py` (Listenfilter), `texts.py` (alle deutschen Meldungen), `lifecycle.py` (Zeitplan und Übergabe beim Start und Ende), `routes/` mit einem Modul je Bereich (Posteingang, Abruf, Nachricht, Absender, Korrektur, Übergabe, Briefing, Listen, Sprache, Dokumente, Beenden) |
| `security/` | `Secret`, Windows-Anmeldeinformationsspeicher, TLS-Kontext, geschwärztes Logging |
| `sample_data.py` | 13 künstliche Testmails aus dem Pflegealltag |

## Erweiterungspunkte

- **Neue Quelle** (z. B. Microsoft 365): `MailSource.plan()` und `load()` implementieren.
- **Andere KI**: `Analyzer.analyze()` implementieren und in `service/setup.py` (`build_analyzer`) eintragen.
- **Andere Ablage** (Netzlaufwerk, Pflegesoftware): `DocumentStore` austauschen.

## Datenhaltung

- `daten/mail_agent.sqlite3`: Nachrichten inkl. Rohdaten (für „Neu analysieren“),
  Analysen (seit Schema 2 mit Fristen, Zahlungsdaten, Zuständigkeit, Konfidenz, Laufzeit),
  bearbeitete Entwürfe, Korrekturen, Dokument-Metadaten; seit Schema 4 die Markierung „KI
  ausstehend“, die Abrufposition je Postfach (`fetch_state`), das Abrufprotokoll (`fetch_runs`) und
  der zuletzt erledigte Zeitplan-Termin (`app_state`). WAL-Modus, damit die Seite lesen kann,
  während der Abruf schreibt. Ältere Datenbanken werden beim Start automatisch ergänzt.
- `daten/eval-berichte/`: JSON-Berichte der Testsammlung.
- `daten/ablage/…`: abgelegte Anhänge und `herkunft.json`.
- `daten/eingang/`: `.eml`-Testdaten.
- `daten/uebergabe.json` (je PC): Übergabe-Ordner, eigener Stand, ob dieser PC die Daten hat.
- `daten/<Datenordner>-sicherung-<Zeit>/`: die letzten 2 Stände vor einer Übernahme.
- `daten/agent.log`: Meldungen des im Hintergrund laufenden Agents (ab 1 MB eine alte Generation).

Seit Schema 5 kennt die Datenbank die Spalte `contact_form`. Der Ordner `daten/` ist von Git
ausgeschlossen und darf nicht in einem Cloud-Sync-Ordner liegen; in die Cloud geht nur die
verschlüsselte Übergabe-Datei.

## Technik

Python 3.13 · uv · FastAPI + Jinja2 · SQLite · Anthropic Python SDK (strukturierte Ausgabe mit
Pydantic) · pytest, ruff, mypy (strict). Code und Bezeichner auf Englisch (PEP 8), Oberfläche,
Dokumentation und Fachbegriffe (Kategorien, Ablage) auf Deutsch.
