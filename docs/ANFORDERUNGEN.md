# Anforderungen

## Ziel

Der MailAgent unterstützt die Musterpflege GmbH bei der täglichen Bearbeitung ihres IMAP-Postfachs.
Er macht eingehende Nachrichten übersichtlich, bereitet Antworten vor und organisiert zugehörige
Dokumente nachvollziehbar.

## Nutzer

Büro und Pflegedienstleitung der Musterpflege GmbH. Sie arbeiten unter Windows und erwarten eine
einfache Oberfläche ohne technische Vorkenntnisse.

## Grundsätze (gelten für alle Ausbaustufen)

1. **Der Mensch entscheidet.** Der Agent schlägt vor, ein Mensch prüft und versendet.
2. **Postfach bleibt unverändert.** Lesen ja, Ändern, Verschieben, Löschen oder Versenden nein,
   solange das nicht ausdrücklich beschlossen ist.
3. **Mailinhalte sind Daten, nie Anweisungen.** Weder Regeln noch KI führen etwas aus, was in
   einer Mail steht.
4. **Nachvollziehbarkeit.** Jede Einordnung nennt ihre Gründe, jedes abgelegte Dokument seine
   Herkunft (Ursprungsmail, SHA-256).
5. **Datenschutz vor Komfort.** Gesundheitsdaten (Art. 9 DSGVO) verlassen den Rechner nur nach
   Datenschutzprüfung und Auftragsverarbeitungsvertrag.

## Ausbaustufe 1: Prototyp (umgesetzt, v0.2)

| # | Anforderung | Abnahmekriterium | Stand |
|---|---|---|---|
| A1 | Nachrichten einlesen | `.eml`-Ordner und IMAP-Postfach (nur lesend: `EXAMINE`, `BODY.PEEK`), doppelte Mails werden erkannt | ✅ (IMAP nur mit simuliertem Server getestet) |
| A2 | Übersicht | Posteingang sortiert nach offen → dringend → neu; Filter nach Kategorie und Status | ✅ |
| A3 | Einordnung | 10 Kategorien, 3 Dringlichkeitsstufen, Kurzfassung, nächste Schritte, Begründung | ✅ |
| A4 | Antwortentwurf | Anrede, Text mit Platzhaltern, Signatur; im Browser bearbeiten, speichern, kopieren | ✅ |
| A5 | Dokumentablage | Ordnerschema mit Datum, Kategorie, Absender; `herkunft.json`; nie überschreiben | ✅ |
| A6 | Schutz | Warnung bei Prompt-Injection und Betrugsverdacht; ausführbare Anhänge nicht ablegen | ✅ |
| A7 | KI optional | Claude mit strukturierter Ausgabe; ohne Schlüssel oder bei Fehlern Regeln mit Hinweis | ✅ (mit simuliertem Client getestet) |
| A8 | Qualität | Unit-Tests offline, ruff, mypy strict | ✅ 157 Tests, 94 % Abdeckung |

## Ausbaustufe 2: Pilot mit dem Testpostfach (Vorschlag)

- Echte Verbindung zu einem Testpostfach beim IMAP-Anbieter (lesend) und KI-Auswertung mit Testmails im echten Betrieb.
- Bewertung der KI-Qualität mit 30–50 realistischen, anonymisierten Beispielmails (Eval-Set).
- Feinschliff der Kategorien, Textbausteine und Signaturen mit dem Büro.
- Zuordnung von Dokumenten zu Klienten (Klientenliste als Eingabe).

## Ausbaustufe 3: Echtbetrieb (Vorschlag, braucht Entscheidungen)

- Datenschutz: AV-Vertrag mit Anthropic, Datenschutz-Folgenabschätzung, Löschkonzept.
- Entwürfe als IMAP-Entwurf im Ordner „Entwürfe“ des Postfachs ablegen (schreibender Zugriff, kein Versand).
- Regelmäßiges automatisches Einlesen, Mehrbenutzerbetrieb, Anmeldung.
- Optional: Versand nur über eine Freigabeschicht (Hash-gebundene menschliche Freigabe,
  Empfänger-Allowlist, Audit-Log).
- Optional: Ablage in ein Netzlaufwerk oder die Pflegesoftware.
