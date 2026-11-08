# Entscheidungsprotokoll

Jede Entscheidung mit Datum, Inhalt, *Warum* und *Verworfen*. Nummern werden nie wiederverwendet; neue
Entscheidungen kommen ans Ende. Eine Entscheidung finden: `grep -n "^| D3 " docs/decisions.md`.

| Nr. | Thema | Entscheidung, Warum, Verworfen |
|---|---|---|
| D1 | Vorlage übernommen | Projekt aus der MailAgent-Vorlage erstellt. Grundsätze unverändert: Postfach nur lesend, kein Versand, Mails sind Daten, Arbeit ohne KI möglich. Warum: diese Grundsätze tragen die Sicherheit des ganzen Werkzeugs. |
| D2 | Lizenz | 2026-11-08 (#2): Die Vorlage steht unter der MIT-Lizenz, Copyright Ahnyth; ersetzt die Wahl zwischen PolyForm Noncommercial und PolyForm Small Business. Warum: kostenlos für alle und als Open Source sofort verständlich, auch fürs Portfolio. Die Small-Business-Grenze (unter 100 Personen und rund 1,3 Mio. US-Dollar Umsatz) hätte genau die Zielgruppe ausgeschlossen, und Lizenzgebühren für eine Vorlage sind kaum durchsetzbar. Dass andere die Vorlage weiterverkaufen dürfen, ist bewusst in Kauf genommen. Verworfen: PolyForm (schreckt ab, nicht Open Source), FSL-1.1-MIT (verbietet nur Konkurrenzprodukte, ist aber ungewohnt und erklärungsbedürftig). |
| D3 | Veröffentlichung | 2026-11-08 (#3, #4, #5): Öffentlich als GitHub-Vorlage `Ahnyth/mail-agent`. Die Vorlage gehört allein Ahnyth. Autor aller Commits ist Ahnyth mit No-Reply-Adresse; ältere Commits wurden vor dem ersten Push umgeschrieben, Namen früherer Projekte sind aus Dateien und Historie entfernt, gearbeitet wird in einem frischen Klon. Sicherheitslücken werden vertraulich über GitHub gemeldet (SECURITY.md). Warum: ein einheitlicher Name, und was einmal öffentlich ist, bleibt öffentlich. |
