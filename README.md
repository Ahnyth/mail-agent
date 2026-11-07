# MailAgent

**A local, read-only mail assistant for small offices, powered by your Claude subscription.**
MailAgent reads an IMAP mailbox without ever changing it, sorts every mail (category, priority,
responsibility, deadlines, payment data, fraud warnings), prepares reply drafts and files attachments.
It runs on a Windows PC with a small web interface on `127.0.0.1`, works without AI thanks to a rule-based
fallback, and **never sends a mail**. The example configuration targets a German home-care service; the
user interface and the documentation below are in German. Free for personal and noncommercial use and for small
businesses; larger companies need a commercial license ([LICENSE.md](LICENSE.md)).

---

Der MailAgent unterstützt ein Büro bei der täglichen Bearbeitung eines Postfachs. Er macht eingehende
Nachrichten übersichtlich, bereitet Antworten vor und legt Dokumente nachvollziehbar ab.

> **Grundsätze:** Das Postfach wird nur gelesen. Der Agent versendet **nie** selbst E-Mails.
> Mailinhalte sind Daten, nie Anweisungen. Ohne KI arbeitet er mit Regeln weiter.

Dieses Repository ist eine **Vorlage**: Es enthält ein vollständiges, getestetes Beispiel für einen
ambulanten Pflegedienst („Musterpflege GmbH“, alle Namen erfunden). Wie man es an die eigene Firma
anpasst, steht unten unter [An die eigene Firma anpassen](#an-die-eigene-firma-anpassen).

## Was er kann

| Funktion | Umsetzung |
|---|---|
| Nachrichten einlesen | `.eml`-Ordner (Testdaten) oder ein IMAP-Postfach – **nur lesend** (`EXAMINE`, `BODY.PEEK[]`, Befehls-Allowlist); ab einem Startdatum alles seit dem letzten Abruf, in Portionen mit Fortschrittsanzeige |
| Automatischer Abruf | zu festen Uhrzeiten (z. B. 9:00); Start bei der Windows-Anmeldung unsichtbar im Hintergrund, Symbol neben der Uhr; verpasste Abrufe werden beim Start nachgeholt |
| Zwei PCs | **Übergabe-Datei**: der Arbeitsstand wandert verschlüsselt (AES-256-GCM) über Google Drive oder Dropbox; nur der PC, der die Daten hat, ruft ab |
| Gesendet-Ordner | Antworten werden dort (nur lesend) erkannt; die Mail gilt dann als beantwortet und erledigt |
| Kontaktformular | Mails des Website-Formulars werden nach dem Besucher eingeordnet, Entwurf an seine Adresse |
| Einordnung | 14 Beispiel-Kategorien (Pflegeanfrage, Termin, Kasse/MD, Arzt/Verordnung, Behörde, Intern, Verdacht, Werbung, …), Priorität, Zuständigkeit, Kurzfassung, nächste Schritte, Begründung |
| Fristen und Zahlungen | Fristen mit Datum und `.ics`-Export; Betrag, IBAN (mit Prüfziffer), Fälligkeit, Rechnungsnummer, IBAN-Abgleich mit bekannten Lieferanten, CSV-Export |
| Sicherheit | Warnstufen nach bekannten Absendern: strenge Warnung bei geänderter Bankverbindung, unbekannten Zahlungsaufforderungen, Anmeldelinks und versteckten KI-Anweisungen; ausführbare Anhänge werden nicht abgelegt |
| Antwortentwurf | Vorschlag nach Stil-Leitfaden mit Platzhaltern `[…]`, im Browser bearbeiten, kopieren oder in Outlook öffnen (gesendet wird nur dort); Muster für häufige Anfragen |
| KI ausstehend | Mails, die ohne KI eingeordnet wurden (z. B. Abo-Kontingent erschöpft), sind markiert und lassen sich gesammelt nachholen |
| Korrektur | „Falsch eingeordnet?“ in der Detailansicht; Übernahme als anonymisierter Testfall |
| Einheitlichkeit | die KI sieht, wie frühere Mails desselben Absenders eingeordnet bzw. korrigiert wurden |
| Übersichten | Morgen-Briefing (auch zum Vorlesen), Fristen, Rechnungen, Bewerbungen mit Checkliste, Wiedervorlage |
| Anhänge | PDF-Text und Texterkennung für Scans (Tesseract); Ablage unter `daten/ablage/<Jahr>/…` mit `herkunft.json` |
| Vorlesen und Sprache | deutsche Windows-Stimme offline; optional Piper und lokale Spracherkennung mit faster-whisper |
| Testsammlung | 79 erfundene Mails mit Soll-Ergebnis, Auswertung per `python -m mail_agent.eval` |

## Voraussetzungen

- Windows 10 oder 11 (der Postfachzugang nutzt den Windows-Anmeldeinformationsspeicher)
- [uv](https://docs.astral.sh/uv/) und Python 3.13
- ein IMAP-Postfach
- für die KI: [Claude Code](https://claude.com/claude-code) mit einem Claude-Abo (Pro oder Max). Ohne
  Abo läuft der Agent mit `engine = "rules"` vollständig lokal.

## Schnellstart (Windows)

**PowerShell** öffnen. Der erste Befehl installiert Git, der zweite holt das Projekt
(`<github-benutzer>` durch das eigene Konto bzw. den Ort des eigenen Forks ersetzen):

```powershell
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements; $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
```

```powershell
git clone https://github.com/<github-benutzer>/mail-agent.git "$env:USERPROFILE\mail-agent"
```

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\mail-agent\scripts\einrichten.ps1"
```

[`scripts/einrichten.ps1`](scripts/einrichten.ps1) installiert uv und Claude Code, richtet Python ein,
legt `mail_agent.toml` aus der Beispielkonfiguration an, fragt das Postfach-Passwort (verdeckt über
`cmdkey`) und die Claude-Anmeldung ab, führt die Tests aus und legt die Desktop-Verknüpfung
**„MailAgent“** sowie den Autostart an. Es kann jederzeit erneut laufen, z. B. zum Aktualisieren.

Solange der Agent läuft, steht unten rechts neben der Uhr ein violettes **M**: Klick öffnet den Agent,
**Rechtsklick → Beenden** beendet ihn (Windows 11 legt neue Symbole zuerst hinter den Pfeil **^**; von
dort einmal neben die Uhr ziehen). Beenden geht auch oben rechts im Agent mit **⏻ Beenden**.

**Ohne Postfach ausprobieren:** in `mail_agent.toml` `[source] type = "eml"` und
`[analysis] engine = "rules"` setzen, dann:

```bash
uv run mail-agent testdaten
```

```bash
uv run mail-agent web
```

und <http://127.0.0.1:8765> öffnen.

## An die eigene Firma anpassen

| Was | Wo |
|---|---|
| Firmenname, Signatur | `[organization]` in `mail_agent.toml` |
| Postfach (Server, Benutzer, Eintrag im Anmeldeinformationsspeicher) | `[imap]` in `mail_agent.toml` |
| Eigene Domains, bekannte Absender (Kassen, Lieferanten mit IBAN, Newsletter …) | [config/bekannte_absender.yaml](config/bekannte_absender.yaml); im Betrieb einfacher per Knopf „Absender als bekannt hinterlegen“ |
| Schreibstil der Entwürfe | [config/stil_leitfaden.md](config/stil_leitfaden.md) |
| Feste Antworten und Muster | [config/vorlagen/](config/vorlagen/) |
| Kategorien, Zuständigkeiten | `Category` und `Responsible` in [src/mail_agent/models.py](src/mail_agent/models.py), Stichwortregeln in [src/mail_agent/analysis/rules.py](src/mail_agent/analysis/rules.py), Hinweise für die KI in [src/mail_agent/analysis/claude.py](src/mail_agent/analysis/claude.py) |
| Kontaktformular der eigenen Website | [src/mail_agent/contact_form.py](src/mail_agent/contact_form.py) |
| Testsammlung | [tests/eval/](tests/eval/): eigene erfundene Fälle ergänzen und vor jeder Regel- oder Prompt-Änderung auswerten |

Nach jeder Änderung: `uv run pytest`, `uv run ruff check src tests`, `uv run mypy src tests`.

## Datenschutz

Der Agent läuft lokal; Maildaten bleiben auf dem PC bzw. verschlüsselt im Übergabe-Ordner. Mit
`engine = "claude-code"` gehen Mailinhalte zur Auswertung an Claude (Anthropic). **Vor dem Einsatz mit
echten Mails selbst klären:** Ist das für die eigenen Daten zulässig (bei Gesundheitsdaten Art. 9
DSGVO), gibt es die nötigen Vereinbarungen, ist das Team informiert? Bis dahin `engine = "rules"`
nutzen oder `allow_mailbox_data = false` lassen. Details und Schutzmaßnahmen:
[docs/SICHERHEIT_UND_DATENSCHUTZ.md](docs/SICHERHEIT_UND_DATENSCHUTZ.md).

## Mit Claude Code weiterentwickeln

Das Projekt ist so aufgebaut, dass [Claude Code](https://claude.com/claude-code) es zuverlässig
weiterentwickeln kann:

- [CLAUDE.md](CLAUDE.md) und [Guidelines.md](Guidelines.md) enthalten die Arbeitsvereinbarung (Rolle,
  feste Regeln, Code-Regeln nach PEP 8 und Microsofts Python-Vorgaben, Definition of Done). Die
  Platzhalter `<Inhaber>` und `<Firma>` durch die eigenen Angaben ersetzen.
- [TODO.md](TODO.md) sagt, wo es weitergeht; [OPEN_ITEMS.md](OPEN_ITEMS.md) sammelt nummerierte Fragen
  an den Inhaber; [docs/decisions.md](docs/decisions.md) hält jede Entscheidung mit Warum fest.
- Die Tests laufen vollständig offline; KI und IMAP werden simuliert.

## Weitere Einstellungen

- **Abruf** (`[fetch]`): `start_date` (ältere Mails werden nie geholt), `times = ["09:00"]`
  (automatischer Abruf), `enabled` (ohne Übergabe-Ordner nur auf dem einen abrufenden PC `true`).
- **Zwei PCs**: auf beiden PCs Google Drive für Desktop (oder Dropbox) mit demselben Konto, im Agent
  unter **Übergabe** denselben Ordner wählen, am anderen PC „Übergabe übernehmen“. Verschlüsselt wird
  mit dem Postfach-Passwort, das auf beiden PCs per `cmdkey` gleich hinterlegt sein muss.
- **Claude** (`[analysis]`): `engine = "claude-code"` nutzt `claude -p` mit dem Abo (keine API-Kosten);
  Modelle, Gründlichkeit (`effort_*`), „erst Regeln“ (`rules_first`, `rules_threshold`) und Parallelität
  sind einstellbar. Ist das Kontingent erschöpft, ordnen die Regeln ein und die KI wird später
  nachgeholt.
- **Texterkennung**: `winget install --id UB-Mannheim.TesseractOCR -e` (Sprache „German“ wählen).
- **Vorlesen mit Piper** (GPL-3.0, optional): `uv sync --inexact --extra stimme`;
  **Sprachsteuerung**: `uv sync --inexact --extra sprache --extra stimme`.

Alle Optionen sind in [mail_agent.example.toml](mail_agent.example.toml) kommentiert. Passwörter und
Schlüssel gehören **nie** in diese Datei.

## Entwicklung

```bash
uv run pytest
```

```bash
uv run ruff check src tests
```

```bash
uv run mypy src tests
```

Testsammlung auswerten (Details: [tests/eval/README.md](tests/eval/README.md)):

```bash
uv run python -m mail_agent.eval --modus regeln
```

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [docs/ANFORDERUNGEN.md](docs/ANFORDERUNGEN.md) | Ziel, Nutzer, Funktionsumfang, Abnahmekriterien |
| [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md) | Komponenten, Datenfluss, Datenhaltung |
| [docs/SICHERHEIT_UND_DATENSCHUTZ.md](docs/SICHERHEIT_UND_DATENSCHUTZ.md) | Schutzmaßnahmen, DSGVO, Restrisiken |
| [docs/tooling-quirks.md](docs/tooling-quirks.md) | Stolpersteine je Werkzeug |
| [CLAUDE.md](CLAUDE.md), [Guidelines.md](Guidelines.md) | Arbeitsvereinbarung für Claude Code |

## Lizenz

Kostenlos für private und gemeinnützige Nutzung und für kleine Firmen; größere Firmen brauchen eine eigene Lizenz.
Genau: Das Projekt steht wahlweise unter der **PolyForm Noncommercial License 1.0.0** oder der **PolyForm Small
Business License 1.0.0**. Die Übersicht steht in [LICENSE.md](LICENSE.md), die verbindlichen Texte in
[LICENSES](LICENSES/).

| Wer | Nutzen, ändern, weitergeben |
|---|---|
| Privatleute (nicht geschäftlich) | ja, kostenlos |
| Gemeinnützige Organisationen, Schulen, Forschung, Behörden (jede Größe) | ja, kostenlos |
| Firmen und Selbstständige mit weniger als 100 Personen **und** weniger als 1 Mio. US-Dollar Umsatz im Vorjahr (Wert von 2019, an die Inflation angepasst) | ja, kostenlos |
| Alle anderen Firmen | nur mit einer eigenen Lizenz vom Inhaber |

Geänderte Fassungen muss niemand veröffentlichen. Wer die Software weitergibt, gibt die Lizenztexte (oder ihren
Link) und die Zeilen „Required Notice:“ aus [LICENSE.md](LICENSE.md) mit. Das Projekt ist „Quelltext offen“
(source-available), aber nicht Open Source im Sinne der OSI, weil größere Firmen zahlen. Die Lizenzen der
genutzten Pakete stehen in [Guidelines.md](Guidelines.md), Abschnitt 8.
