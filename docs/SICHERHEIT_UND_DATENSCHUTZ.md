# Sicherheit und Datenschutz

## Schutzmaßnahmen im Prototyp

### Postfach

- **Nur lesen, technisch erzwungen:** Das Postfach wird mit `EXAMINE` geöffnet (read-only nach
  RFC 3501) und Nachrichten werden mit `BODY.PEEK[]` abgerufen. Dadurch ändert sich auch der
  Gelesen-Status nicht. Eine Allowlist (`sources/imap.py`) lässt nur `CAPABILITY`, `LOGIN`, `NOOP`,
  `LOGOUT`, `EXAMINE`, `UID SEARCH` und `UID FETCH (BODY.PEEK[])` durch. Alles andere
  (`STORE`, `COPY`, `MOVE`, `EXPUNGE`, `APPEND`, `SELECT`, `DELETE` …) wird abgelehnt, bevor es den
  Socket erreicht.
- **Gesendet-Ordner:** wird genauso nur lesend geöffnet, um Antworten zu erkennen; gefunden
  wird er durch Ausprobieren üblicher Namen mit `EXAMINE` (`LIST` bleibt verboten). Von gesendeten
  Mails wird nur der Kopf gelesen, nichts gespeichert und nichts an die KI geschickt.
- **Kontaktformular:** Name und Adresse aus dem Formular der Website hat der Besucher selbst
  eingetragen. Solche Absender gelten nie als bekannt oder intern und lassen sich nicht hinterlegen;
  IP-Adresse und Browserkennung gehen nicht an die KI.
- **Kein Versand:** Es gibt keinen SMTP-Code.
- **Richtiges Postfach:** Der Benutzername aus dem Anmeldeinformationsspeicher muss
  `allowed_username` entsprechen. Andernfalls bricht der Agent ab, bevor er eine Verbindung aufbaut.
- **TLS:** Zertifikats- und Hostnamenprüfung sind Pflicht, mindestens TLS 1.2. Ein ungeprüfter
  Kontext wird abgelehnt.
- **Zugangsdaten:** Sie liegen nur im Windows-Anmeldeinformationsspeicher und nie in Dateien oder
  Logs. Passwörter stecken in einer `Secret`-Hülle, Logs werden geschwärzt (E-Mail-Adressen,
  `LOGIN`), Tracebacks werden nicht protokolliert.

### KI (Claude)

- Die Mail wird als abgegrenzter Datenblock übergeben. Der System-Prompt verbietet, darin
  enthaltene Anweisungen zu befolgen. Versuche, den Block zu schließen (`</email>`), werden
  entschärft.
- Das Modell bekommt **keine Werkzeuge** und kann daher nichts ausführen.
- Die Ausgabe ist auf ein festes Schema beschränkt (strukturierte Ausgabe, geschlossene
  Kategorien). Jeder Entwurf wird von einem Menschen geprüft.
- **Anhänge:** Der Text wird lokal ausgelesen (PDF-Textebene mit pdfium, Scans per Tesseract als
  eigener Prozess mit Zeitlimit; nichts wird ausgeführt). Der ausgelesene Text geht gekürzt
  (höchstens ca. 10.000 Zeichen je Mail) mit an die KI, damit sie prüfen kann, ob der Anhang passt –
  bei echten Mails also erst, wenn der Inhaber die KI freigegeben hat (siehe Datenschutz). Abschalten:
  `analysis.send_attachment_text = false` (dann nur Dateinamen) bzw. `attachments.read_text = false`
  (gar kein Auslesen).
- Der Anhangtext steht wie der Mailtext im abgegrenzten Datenblock; feste Prüfungen (IBAN-Abgleich,
  Bankverbindungsänderung, Fristen) werten ihn ebenfalls aus.
- Lehnt das Modell ab (`refusal`), greift serverseitig ein Ersatzmodell (`fallbacks: "default"`)
  und lokal die Regel-Engine.

### Exporte und Sprachausgabe

- Kalenderdateien (`.ics`) enthalten nur Frist, Betreff, Absender und den lokalen Link – sie sind
  ein Vorschlag zum Importieren; nichts wird automatisch eingetragen. Achtung: Wer sie in einen
  Cloud-Kalender importiert, überträgt Betreff und Absender dorthin.
- Der CSV-Export neutralisiert Zellen, die mit `= + - @` beginnen (keine Formeln aus Mailinhalten).
- Vorlesen: Browser-Stimme (`speechSynthesis`, unter Windows lokal) oder Piper (offline). Gelesen
  werden nur Kategorie, Absender, Betreff bzw. ein KI-Vorlesesatz ohne Namen und Gesundheitsdaten,
  nie der Mailtext. Erzeugtes Audio wird nicht gespeichert.
- Piper (`piper-tts`) steht unter GPL-3.0-or-later und ist deshalb nur ein optionales Zusatzpaket
  (`uv sync --extra stimme`); die Stimme `de_DE-thorsten-high` (Datensatz CC0) liegt in
  `daten/stimmen/` und wird nicht eingecheckt.

### Sprachsteuerung

- Spracherkennung **nur lokal** mit faster-whisper; die Spracherkennung des Browsers (bei Chrome:
  Google-Server) wird nicht verwendet.
- Push-to-talk: Aufnahme nur, solange der Knopf gedrückt ist, höchstens 30 Sekunden bzw. 6 MB. Die
  WAV-Daten werden im Arbeitsspeicher dekodiert und verworfen, nie gespeichert. Im Protokoll steht
  nur die Art des Befehls, nicht das Transkript.
- Mikrofon per `Permissions-Policy` nur für die eigene Seite; der Endpunkt unterliegt derselben
  Same-Origin-Prüfung wie alle Formulare.
- Befehle werden mit festen Regeln erkannt (keine KI). „Antworte: …“ erzeugt nur einen Entwurf. Erst
  der Knopf „Von KI formulieren lassen“ schickt den Entwurf mit der Mail an die KI.

### Dokumente

- Dateinamen werden bereinigt: keine Pfadanteile, keine reservierten Windows-Namen. Dateien
  werden exklusiv geschrieben und nie überschrieben.
- Ausführbare und makrofähige Formate (`.exe`, `.js`, `.ps1`, `.docm` …, auch als `x.pdf.exe`)
  werden nicht abgelegt.
- Downloads sind nur aus dem Ablageordner möglich. Nur PDF und Bilder werden im Browser angezeigt,
  alles andere wird heruntergeladen.

### Weboberfläche

- Sie läuft ausschließlich auf `127.0.0.1`, und nur `localhost`/`127.0.0.1` werden als Host
  akzeptiert (Schutz gegen DNS-Rebinding).
- Ändernde Anfragen werden nur von derselben Herkunft angenommen (CSRF-Schutz). Das gilt auch für
  „Beenden“ (`POST /beenden`): eine fremde Webseite kann den Agent nicht abschalten.
- Content-Security-Policy ohne Inline-Skripte, Auto-Escaping aller Mailinhalte, `no-store`-Cache.

## Datenschutz (DSGVO)

Mails eines Pflegedienstes enthalten oft **Gesundheitsdaten**; das sind besondere Kategorien
personenbezogener Daten nach Art. 9 DSGVO. Bevor echte Mails an Claude gehen, klärt der Inhaber
(am besten mit seinem Datenschutzbeauftragten):

- die **Rechtsgrundlage** für die Verarbeitung durch einen KI-Dienst,
- einen **Auftragsverarbeitungsvertrag** (AV-Vertrag) mit dem Anbieter (ein privates Abo hat keinen),
- die **Übermittlung in ein Drittland** (Anthropic sitzt in den USA),
- ob eine **Datenschutz-Folgenabschätzung** (DSFA) nötig ist.

Bis dahin bleibt `engine = "rules"` (so steht es in `mail_agent.example.toml`): Dann verlässt keine
Mail den PC. Zusätzlich lehnt der Agent ein IMAP-Postfach mit KI ab, solange
`allow_mailbox_data = false` ist. Die erfundenen Testdaten (`mail-agent testdaten`) enthalten keine
echten Personen.

| Thema | Stand | Noch offen |
|---|---|---|
| Daten an Claude (Anthropic, USA) | in der Vorlage aus: `engine = "rules"`, `allow_mailbox_data = false`; erst nach Freigabe durch den Inhaber `engine = "claude-code"` und `allow_mailbox_data = true` | vor der Freigabe klären: Rechtsgrundlage, AV-Vertrag (bei Gesundheitsdaten z. B. ein gewerbliches Abo), Drittlandtransfer, Datenschutz-Folgenabschätzung |
| Lokale Speicherung | SQLite und Ablage unverschlüsselt in `daten/` auf dem PC | ob die Festplatte verschlüsselt wird (z. B. BitLocker), entscheidet der Inhaber; empfohlen bei Gesundheitsdaten und Laptops |
| Übergabe zwischen den PCs | eine Datei im Cloud-Ordner (Google Drive/Dropbox), AES-256-GCM, Schlüssel per scrypt aus dem Postfach-Passwort; nur PC-Name, Stand und Zeit liegen lesbar daneben | Wer das Postfach-Passwort kennt, kann die Datei lesen – genau wie das Postfach selbst |
| Sicherung | die Übergabe-Datei ist zugleich eine Sicherung; beim Übernehmen bleiben die letzten 2 Stände als Ordner | Sicherung außerhalb der Cloud, falls gewünscht |
| Löschung | manuell | Löschkonzept mit Fristen (Rohdaten, Entwürfe, Ablage) |
| Zugriff | Einzelplatz, keine Anmeldung | Rollen und Anmeldung bei Mehrbenutzerbetrieb |
| Protokolle | geschwärzt, keine Inhalte | Aufbewahrungsfristen festlegen |

## Bekannte Grenzen

- Python-Strings lassen sich nicht sicher aus dem Speicher löschen (Passwort im Arbeitsspeicher).
- Andere Prozesse desselben Windows-Benutzers können generische Anmeldeinformationen lesen.
- Die Regel-Engine erkennt Betrugsversuche nur an typischen Formulierungen.
