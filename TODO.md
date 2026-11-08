# MailAgent: Fahrplan

Die Regeln stehen in [Guidelines.md](Guidelines.md) (Definition of Done in Abschnitt 7.5). Aufgaben abhaken,
sobald sie fertig sind. Fragen an den Inhaber sind in [OPEN_ITEMS.md](OPEN_ITEMS.md) nummeriert; Entscheidungen
stehen in [docs/decisions.md](docs/decisions.md). Nirgends Zeitschätzungen.

## Jetzt und als Nächstes

Sagt immer, wo eine neue Sitzung weitermacht. Kurz halten.

- **Pflege der Vorlage:** veröffentlicht als öffentliche GitHub-Vorlage `Ahnyth/mail-agent` (MIT, D2 bis D5).
  Nichts offen; Verbesserungen stehen unter „Ideen für später“.
- **Für eine neue Firma:** die Vorlage an die eigene Firma anpassen (README „An die eigene Firma anpassen“):
  Platzhalter in CLAUDE.md und Guidelines.md, `mail_agent.toml`, bekannte Absender, Stil-Leitfaden, Vorlagen. Danach
  mit `[source] type = "eml"` und Testdaten ausprobieren, dann das echte Postfach nur lesend anbinden.
- **Wartet auf den Inhaber:** siehe OPEN_ITEMS.md.

---

## Ideen für später

- Kategorien und Zuständigkeiten per Konfigurationsdatei statt im Code, damit andere Branchen die Vorlage ohne
  Programmierung anpassen können.
- Erkennung des Kontaktformulars aus dem Firmennamen in der Konfiguration ableiten.
- Tests bei jedem Push auf GitHub laufen lassen (GitHub Actions, Windows): ein grünes Abzeichen in der README zeigt
  Besuchern sofort, dass die Vorlage funktioniert.
- GitHub Sponsors als Spendenweg; Geldfrage, entscheidet der Inhaber.
- Bildschirmfotos mit den Beispieldaten in die README: Besucher sehen in Sekunden, was die Vorlage tut.
- Bekanntmachen, falls der Inhaber es doch will (D5): Repo im Profil anheften und auf der Website verlinken,
  LinkedIn-Beitrag, r/selfhosted und r/ClaudeAI, IT-Dienstleister für Pflegedienste, Praxen und Kanzleien.
