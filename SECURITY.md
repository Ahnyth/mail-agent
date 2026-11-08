# Sicherheit

Der MailAgent liest Postfächer mit vertraulichen Daten. Sicherheitslücken bitte **nicht** als öffentliches Issue
melden, sondern vertraulich über GitHub: im Repository unter **Security → Report a vulnerability**
(<https://github.com/Ahnyth/mail-agent/security/advisories/new>).

Besonders wichtig sind Lücken, durch die

- die Nur-lesen-Garantie des Postfachs umgangen wird (Befehls-Allowlist in `src/mail_agent/sources/imap.py`),
- Mailinhalte als Anweisungen an die KI wirken statt als Daten,
- die Weboberfläche oder Daten über `127.0.0.1` hinaus erreichbar werden,
- Zugangsdaten oder Mailinhalte in Protokolle, Dateien oder Git gelangen.

Unterstützt wird nur der aktuelle Stand von `main`.

---

Please report security issues privately via GitHub's **Report a vulnerability** form linked above, not as public
issues. Only the latest `main` is supported.
