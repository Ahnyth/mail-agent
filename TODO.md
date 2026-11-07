# MailAgent: Fahrplan

Die Regeln stehen in [Guidelines.md](Guidelines.md) (Definition of Done in Abschnitt 7.5). Aufgaben abhaken,
sobald sie fertig sind. Fragen an den Inhaber sind in [OPEN_ITEMS.md](OPEN_ITEMS.md) nummeriert; Entscheidungen
stehen in [docs/decisions.md](docs/decisions.md). Nirgends Zeitschätzungen.

## Jetzt und als Nächstes

Sagt immer, wo eine neue Sitzung weitermacht. Kurz halten.

- **Als Nächstes:** die Vorlage an die eigene Firma anpassen (README „An die eigene Firma anpassen“): Platzhalter
  in CLAUDE.md und Guidelines.md, `mail_agent.toml`, bekannte Absender, Stil-Leitfaden, Vorlagen. Danach mit
  `[source] type = "eml"` und Testdaten ausprobieren, dann das echte Postfach nur lesend anbinden.
- **Wartet auf den Inhaber:** siehe OPEN_ITEMS.md.

---

## Ideen für später

- Kategorien und Zuständigkeiten per Konfigurationsdatei statt im Code, damit andere Branchen die Vorlage ohne
  Programmierung anpassen können.
- Erkennung des Kontaktformulars aus dem Firmennamen in der Konfiguration ableiten.
