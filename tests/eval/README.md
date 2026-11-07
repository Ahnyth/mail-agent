# Testsammlung (Eval)

Erfundene Beispielmails mit Soll-Ergebnis, um Änderungen an Prompt, Regeln oder Modellwahl zu
messen statt zu schätzen. **Keine echten Personen, Patienten, Gesundheitsdaten oder Bankdaten.**
Alle Domains enden auf `.example` (RFC 2606).

```
tests/eval/
  faelle/                  ein Fall pro Datei (*.yaml), eingecheckt
  neu/                     aus der Weboberfläche übernommene Korrekturen (anonymisiert, NICHT eingecheckt)
  bekannte_absender.yaml   Absenderliste nur für die Auswertung
```

## Auswerten

```bash
uv run python -m mail_agent.eval                       # wie konfiguriert (erst Regeln, dann KI)
uv run python -m mail_agent.eval --modus regeln        # nur Regeln, keine Daten an eine KI
uv run python -m mail_agent.eval --modus ki --parallel 3
uv run python -m mail_agent.eval --nur bescheid        # nur Fälle, deren ID "bescheid" enthält
```

Ausgabe: Trefferquote je Feld, unnötige Entwürfe, Laufzeit pro Mail und die Liste der Abweichungen.
Ein JSON-Bericht landet unter `daten/eval-berichte/`.

## Aufbau eines Falls

```yaml
# Erfundener Testfall – alle Personen, Firmen und Daten sind fiktiv.
id: kasse-kostenzusage-01            # = Dateiname ohne .yaml
beschreibung: Kostenzusage der AOK für Behandlungspflege
tags: [kostentraeger, anhang]        # optional, frei wählbar (z. B. weiterleitung, phishing)
mail:
  von: "AOK Musterland <leistungen@aok-musterland.example>"
  betreff: "Kostenzusage Behandlungspflege"
  datum: "2026-10-20T09:15:00+02:00"
  html: false                        # optional
  anhaenge:                          # optional: Dateiname oder {name, text} für ein PDF mit Textebene
    - Kostenzusage_2026-10.pdf
    - name: Rechnung_2026-311.pdf
      text: |
        Rechnung Nr. 2026-311 …
  text: |
    Sehr geehrte Damen und Herren,
    …
soll:
  kategorie: kasse_md                # Pflicht
  prioritaet: normal                 # Pflicht: hoch | normal | niedrig
  entwurf: false                     # Pflicht: Antwortentwurf sinnvoll?
  zustaendig: pdl                    # optional: pdl | abrechnung | personal | geschaeftsfuehrung | verwaltung
  warnung: false                     # optional: Sicherheitswarnung (Betrug/Phishing) erwartet?
  frist: true                        # optional: mindestens eine Frist erwartet?
  anhang_passt: false                # optional: passt der Anhang zum Inhalt der Mail?
```

Kategorien: `pflegeanfrage`, `termin`, `kasse_md`, `arzt_verordnung`, `abrechnung`, `bewerbung`,
`beschwerde`, `unterlagen`, `behoerde`, `intern`, `verdacht`, `werbung`, `sonstiges`.
