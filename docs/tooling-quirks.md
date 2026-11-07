# Stolpersteine der Werkzeuge

Nach Werkzeug geordnet. **Den Teil eines Werkzeugs vor dessen Benutzung lesen**; einen neuen Stolperstein sofort
unter seiner Überschrift eintragen, mit dem Warum (was passiert ist, was zu tun ist).

## Windows, Claude-Desktop-App und Shells

- **Die Claude-Desktop-App ist ein MSIX-Paket**: Was Claude unter `%APPDATA%` schreibt (z. B. `uv python install`),
  landet umgeleitet in `AppData\Local\Packages\Claude_…` und ist für die eigenen Shells des Nutzers unsichtbar.
  Ein `.venv` auf so einem Python machte die Desktop-Verknüpfung kaputt („virtual environment linked to
  non-existent Python interpreter“). **Werkzeuge per winget installieren**, nie `uv python install` aus der App.
- **Python-Version festlegen**: `.venv` soll das per winget installierte Python 3.13 unter
  `%LOCALAPPDATA%\Programs\Python\Python313\python.exe` nutzen, auch wenn das System-`python` neuer ist. Neu
  anlegen mit `uv sync --inexact --python <dieser Pfad>`.
- **Ein laufender Agent sperrt `.venv`** und Port 8765. Vor `uv sync` oder `einrichten.ps1` den Agent beenden;
  Vorschau-Server (Claude) und Desktop-Verknüpfung nutzen denselben Port.
- **Was der Nutzer wirklich sieht**, prüft man mit einem Prozess außerhalb des App-Pakets:
  `Invoke-CimMethod Win32_Process Create`, Ausgabe in eine Datei außerhalb von `%APPDATA%` schreiben (z. B. in
  den Projektordner). Genauso für alles, was im Windows-Profil des Nutzers landen muss (z. B. die
  Autostart-Verknüpfung unter `%APPDATA%\...\Startup`): aus der App heraus geschrieben, wäre sie für Windows
  unsichtbar.
- **Hintergrundstart mit `pythonw`**: hat kein stdout/stderr; uvicorn bricht ohne ab
  (`sys.stdout.isatty()`). `background.py` leitet beides nach `daten/agent.log` um. Jedes von
  `pythonw` gestartete Konsolenprogramm (Claude Code, Tesseract, PowerShell) öffnet ohne
  `CREATE_NO_WINDOW` ein eigenes Fenster (`processes.NO_WINDOW`).
- **Symbol neben der Uhr (`pystray`)**: läuft in einem eigenen Daemon-Thread; pystrays Hilfsthread erbt das,
  sonst hielte er den Prozess nach dem Beenden am Leben. `Icon.stop()` wirkt erst, wenn das Symbol bereit ist,
  darum prüft `_setup` ein frühes Stoppen nach. Ein hart beendeter Prozess (`Stop-Process`) hinterlässt ein totes
  Symbol, bis die Maus darüberfährt; darum beendet `einrichten.ps1` zuerst sanft über `POST /beenden`.
  Windows 11 legt neue Symbole hinter den Pfeil „^“ und führt sie in den Einstellungen unter dem Programmnamen
  „pythonw.exe“/„Python“, nicht unter „MailAgent“. Ob Windows das Symbol angenommen hat, zeigt (nur lesend)
  `HKCU:\Control Panel\NotifyIconSettings` mit dem Tooltip „MailAgent läuft …“.
- **Bildschirmfotos aus der Shell** (`CopyFromScreen`) schlagen mit „Das Handle ist ungültig“ fehl, solange der
  Bildschirm gesperrt ist (`LogonUI` läuft in der Sitzung). Dann Weboberfläche über das Browser-Fenster der App
  prüfen und Symbol-Klicks dem Nutzer überlassen.
- **uvicorn sauber stoppen**: `server.should_exit = True` (aus `cli._cmd_web`); `timeout_graceful_shutdown` begrenzt
  das Warten auf offene Anfragen (10 s), danach stoppt die Lifespan Zeitplan und Auftrag und schreibt die Übergabe.
- **Shell-Werkzeug von Claude**: Heredocs mit Umlauten an `python -` können falsch dekodiert werden
  (Ersetzungen greifen dann nicht). Änderungsskripte mit Umlauten als Datei schreiben und ausführen.
  Python schreibt auf Windows mit `write_text` CRLF; mit `newline="\n"` schreiben.
- **Umlaute in der Git-Bash-Ausgabe** erscheinen als `�` (Codepage). Die Daten sind in Ordnung; Inhalte mit Python
  gegen die Datenbank prüfen, nicht am Konsolenbild.
- **Zeilenenden**: `core.autocrlf = true`; `.gitattributes` legt LF fest, `.ps1` und `.cmd` CRLF (cmd.exe verträgt
  LF bei Sprungmarken nicht zuverlässig).

## Claude Code als Analyse-Engine (`claude -p`)

- Claude Code bevorzugt ein `ant auth login`-Profil (API-Konto ohne Guthaben) vor der Abo-Anmeldung. Der Aufruf setzt
  deshalb `ANTHROPIC_CONFIG_DIR` auf einen leeren Ordner. Abgelaufene Anmeldung: `claude auth login --claudeai`.
- Jeder Aufruf zählt auf **dasselbe Abo-Kontingent** wie die Chat-Sitzungen; vor großen Abrufen oder
  Neuauswertungen den Verbrauch prüfen.
- Effort wird je Aufruf mit `--effort` gesetzt (`analysis.effort_classify`, `analysis.effort_draft`).

## Python-Pakete

- **pdfium ist nicht threadsicher**: beim parallelen Einlesen stürzte der Prozess ab; alle pdfium-Aufrufe laufen
  unter einer Sperre.
- **PyAV wird von der Windows-Anwendungssteuerung blockiert** (DLL von `av`): Audio dekodiert die App selbst
  (`wave`/NumPy), ein Platzhaltermodul ersetzt `av`.
- `numpy` ist feste Abhängigkeit (Sprachsteuerung lädt es beim Start); `ctranslate2` auf 4.8.3 festgelegt
  (4.8.5 war erst einen Tag alt).
- **Tesseract per winget** bringt kein Deutsch mit: `deu.traineddata` aus `tesseract-ocr/tessdata` ergänzen.

## Browser und Vorlesen

- Im Chrome des Nutzers startete `speechSynthesis` nie, ein frisches Test-Chrome sprach. Deshalb erzeugt der Agent
  die Sprache selbst (Windows-Stimme, WAV).
- Hört man nichts, obwohl „Liest vor …“ erscheint: Windows-Tonausgabe prüfen (welches Gerät ist Standard?).
- Der PDF-Viewer von Edge/Chrome zeigt mit `object-src 'none'` eine leere Seite; PDFs bekommen eine eigene CSP.

## GitHub

- Commits mit der privaten Mailadresse lehnt GitHub ab, wenn sie im Konto als privat geschützt ist: immer die
  No-Reply-Adresse des eigenen GitHub-Kontos nutzen (`<id>+<name>@users.noreply.github.com`).
- Das GitHub-MCP der Claude-App kann fehlschlagen („Authorization header is badly formatted“); `git` und `gh` gehen.
- **Keine Maildaten ins Repository**: einzelne Dateien über 100 MB lehnt GitHub ab, und die Datenbank eines
  großen Postfachs kommt schnell in diese Größe. Dazu kommen Datenschutz und dass Git nichts vergisst.

## IMAP und das Postfach

- Die Abrufposition (UID) gilt nur mit derselben `UIDVALIDITY`; ändert der Server sie, entscheidet das Startdatum und
  die Message-ID verhindert Doppelte.
- **Kontaktformular der Website** (Beispiel: WordPress mit Elementor): Mails „Neue Nachricht von <Website>“ kommen
  vom eigenen Postfach an das eigene Postfach (PHPMailer), ohne `Reply-To`. Der echte Absender steht nur als Feld
  im HTML (Vorname, Nachname, E-Mail-Adresse, Telefon, Ihre Nachricht). Viele davon sind Spam von Bots (Nachricht „on“).
- Ordnernamen kann der Agent nicht auflisten (`LIST` ist nicht in der Allowlist); den Gesendet-Ordner findet er,
  indem er übliche Namen nur lesend mit `EXAMINE` öffnet. Bei manchen Anbietern heißt er
  **„Sent Items“** (Outlook-Name). Namen mit Leerzeichen müssen in Anführungszeichen stehen
  (`imaplib` setzt sie nicht selbst).
- Der Gesendet-Ordner enthält nur, was über dieses Postfach verschickt wurde: Antworten aus einem anderen Konto
  oder Programm sieht der Agent nicht, die Mail bleibt dann offen.

## Google Drive und Dropbox (Übergabe)

- Google Drive für Desktop legt ein eigenes Laufwerk an (meist `G:\Meine Ablage`), Dropbox einen Ordner
  `%USERPROFILE%\Dropbox`; `handover.suggested_folders()` sucht beides.
- Nie die SQLite-Datenbank selbst in einen Sync-Ordner legen (kann beim Synchronisieren beschädigt werden); nur
  die fertige Übergabe-Datei, unter Zwischennamen geschrieben und erst am Ende umbenannt.
