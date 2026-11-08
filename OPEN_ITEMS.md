# Offene Punkte für den Inhaber

Fragen und Aufgaben, die der Inhaber braucht. Jeder Punkt hat eine feste Nummer (`#n`), die nie wiederverwendet
wird; eine Antwort kann also einfach „Zu #3: ja“ lauten. Beantwortete oder erledigte Punkte bekommen eine
Entscheidung in [docs/decisions.md](docs/decisions.md) und werden hier gelöscht.

## Offen

| # | Art | Punkt | Warum es zählt |
|---|---|---|---|
| #1 | Entscheidung | **Echte Mails an Claude?** Vor dem Echtbetrieb klären, ob Mailinhalte an Claude gehen dürfen (Datenschutz, bei Gesundheitsdaten Art. 9 DSGVO). Bis dahin `engine = "rules"` oder `allow_mailbox_data = false`. | Ohne Freigabe bleiben alle Daten lokal. |
| #6 | Aufgabe | **Alten lokalen Ordner löschen.** Gearbeitet wird ab jetzt im frischen Klon `mail-agent`; dort eine neue Claude-Sitzung öffnen. Der alte Ordner trägt noch einen früheren Projektnamen und lässt sich nicht löschen, solange eine Sitzung darin läuft. | Erst dann ist der alte Name auch auf dem PC weg. |
| #7 | Entscheidung | **Bekannt machen.** Empfehlung in dieser Reihenfolge: (1) Projekt im GitHub-Profil anheften und auf der eigenen Website verlinken; (2) ein LinkedIn-Beitrag mit Bildschirmfoto und dem Nutzen für kleine Büros; (3) Beiträge in passenden Communities, z. B. r/selfhosted und r/ClaudeAI; (4) IT-Dienstleister für Pflegedienste, Praxen und Kanzleien direkt ansprechen. Entwürfe schreibt Claude, posten tut der Inhaber. | Firmen suchen nicht auf GitHub; sie finden ein Werkzeug über Menschen, die es ihnen zeigen. |
