# Offene Punkte für den Inhaber

Fragen und Aufgaben, die der Inhaber braucht. Jeder Punkt hat eine feste Nummer (`#n`), die nie wiederverwendet
wird; eine Antwort kann also einfach „Zu #3: ja“ lauten. Beantwortete oder erledigte Punkte bekommen eine
Entscheidung in [docs/decisions.md](docs/decisions.md) und werden hier gelöscht.

## Offen

| # | Art | Punkt | Warum es zählt |
|---|---|---|---|
| #1 | Entscheidung | **Echte Mails an Claude?** Vor dem Echtbetrieb klären, ob Mailinhalte an Claude gehen dürfen (Datenschutz, bei Gesundheitsdaten Art. 9 DSGVO). Bis dahin `engine = "rules"` oder `allow_mailbox_data = false`. | Ohne Freigabe bleiben alle Daten lokal. |
