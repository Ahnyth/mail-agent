"""German texts of the web interface: confirmations, error messages and labels.

All wording the office reads lives here, so it can be checked and changed in one place. The keys are the
short codes that travel in addresses (``?hinweis=…``, ``?fehler=…``) or come from the background jobs.
"""

NOTICES: dict[str, str] = {
    "gespeichert": "Entwurf gespeichert.",
    "neu_analysiert": "Nachricht neu analysiert.",
    "status": "Status geändert.",
    "korrigiert": "Korrektur gespeichert. Sie kann jetzt als Testfall übernommen werden.",
    "testfall": "Anonymisierter Testfall unter tests/eval/neu/ gespeichert – bitte vor der Übernahme prüfen.",
    "beantwortet": "Als beantwortet markiert.",
    "diktat": "Diktierte Antwort als Entwurf gespeichert – bitte prüfen. Es wurde nichts gesendet.",
    "formuliert": "Entwurf von der KI formuliert – bitte prüfen. Es wurde nichts gesendet.",
    "ki_fehler": "Die KI konnte den Entwurf gerade nicht formulieren. Der bisherige Text bleibt erhalten.",
    "unbeantwortet": "Markierung „beantwortet“ entfernt.",
    "abruf_gestartet": "Abruf gestartet – die Nachrichten erscheinen nach und nach.",
    "abruf_laeuft": "Es läuft bereits ein Abruf oder eine KI-Auswertung.",
    "abruf_aus": "Auf diesem PC ist der Abruf ausgeschaltet (nur ein PC ruft das Postfach ab).",
    "ki_gestartet": "KI-Auswertung der markierten Nachrichten gestartet.",
    "diktat_leer": "Das Textfeld ist leer – bitte zuerst Stichworte oder einen Entwurf eintragen.",
    "erledigt_weiter": "Als erledigt markiert – hier ist die nächste offene Nachricht.",
    "alle_erledigt": "Als erledigt markiert – in dieser Liste ist nichts mehr offen.",
    "werbung_erledigt": "{anzahl} Werbe-Mails als erledigt markiert.",
    "absender_gespeichert": "Absender als bekannt hinterlegt. {anzahl} offene Mail(s) dieses Absenders "
    "werden neu ausgewertet.",
    "absender_gespeichert_ohne": "Absender als bekannt hinterlegt. Es läuft gerade ein anderer Auftrag – die "
    "{anzahl} Mail(s) bei Bedarf später mit „Neu analysieren“ auswerten.",
    "absender_entfernt": "Absender aus der Liste entfernt; seine offenen Mails werden neu ausgewertet.",
    "iban_ungueltig": "Die IBAN ist ungültig (Prüfziffer stimmt nicht) – nichts gespeichert.",
    "korrigiert_absender": "Korrektur gespeichert und auf {anzahl} weitere offene Mail(s) dieses Absenders "
    "übernommen.",
    "uebergabe_ordner": "Übergabe-Ordner gespeichert.",
    "uebergabe_geschrieben": "Übergabe geschrieben: Stand {anzahl}. Der andere PC kann jetzt übernehmen.",
    "uebergabe_uebernommen": "Übergabe übernommen: Stand {anzahl}. Dieser PC ruft jetzt das Postfach ab; die "
    "bisherigen Daten liegen als Sicherung daneben.",
}
"""Green confirmation lines after a redirect; ``{anzahl}`` is replaced by the number in the address."""

HANDOVER_ERRORS: dict[str, str] = {
    "handover_not_configured": "Es ist noch kein Übergabe-Ordner eingestellt.",
    "handover_not_active": "Dieser PC hat die Daten nicht – erst die Übergabe übernehmen.",
    "handover_no_package": "Im Übergabe-Ordner liegt keine Übergabe-Datei.",
    "handover_folder_unreadable": "Der Übergabe-Ordner ist nicht erreichbar (Google Drive bzw. Dropbox "
    "angemeldet?).",
    "handover_no_key": "Der Postfachzugang fehlt auf diesem PC (cmdkey) – ohne ihn lässt sich nichts "
    "ver- oder entschlüsseln.",
    "handover_wrong_key": "Die Übergabe-Datei lässt sich nicht entschlüsseln: Ist das Postfach-Passwort auf "
    "beiden PCs gleich hinterlegt?",
    "handover_damaged": "Die Übergabe-Datei ist unvollständig oder beschädigt (Cloud noch beim Hochladen?). "
    "Bitte später erneut versuchen.",
    "handover_newer_format": "Die Übergabe-Datei stammt von einer neueren Version – bitte diesen PC "
    "aktualisieren (einrichten.ps1).",
    "handover_update_first": "Die Daten stammen von einer neueren Version – bitte diesen PC aktualisieren "
    "(einrichten.ps1).",
    "handover_install_failed": "Die Daten konnten nicht ersetzt werden (Datei gerade in Benutzung?). Bitte "
    "erneut versuchen.",
    "handover_busy": "Es läuft gerade ein Abruf oder eine Auswertung – bitte warten, bis er fertig ist.",
}
"""Error codes of ``HandoverError`` as text for the hand-over page."""

SPEECH_ERRORS: dict[str, str] = {
    "audio_too_large": "Die Aufnahme ist zu groß.",
    "audio_too_long": "Die Aufnahme ist zu lang (höchstens 30 Sekunden).",
    "audio_too_short": "Die Aufnahme ist zu kurz – Knopf beim Sprechen gedrückt halten.",
    "audio_invalid": "Die Aufnahme ist keine gültige WAV-Datei.",
    "audio_format": "Das Aufnahmeformat wird nicht unterstützt.",
    "whisper_missing": "Die Spracherkennung ist nicht installiert (uv sync --extra sprache).",
}
"""Error codes of ``SpeechInputError`` as text for the push-to-talk button."""

SOURCE_ERRORS: dict[str, str] = {
    "eml_folder_missing": "Der Eingangsordner für .eml-Dateien existiert nicht.",
    "credential_not_found": "Kein Zugang im Windows-Anmeldeinformationsspeicher eingerichtet.",
    "credential_manager_requires_windows": "Der Postfachzugang funktioniert nur unter Windows.",
    "username_not_allowed": "Der hinterlegte Benutzer entspricht nicht dem erlaubten Postfach.",
    "password_unsupported_characters": "Das Passwort enthält Zeichen, die IMAP-LOGIN nicht unterstützt.",
    "tls_certificate_invalid": "Das Serverzertifikat ist nicht vertrauenswürdig – Verbindung abgebrochen.",
    "imap_connection_failed": "Verbindung zum Mailserver fehlgeschlagen.",
    "imap_login_failed": "Anmeldung am Postfach fehlgeschlagen.",
    "imap_mailbox_unavailable": "Der Posteingang konnte nicht geöffnet werden.",
    "imap_search_failed": "Die Suche im Postfach ist fehlgeschlagen.",
    "imap_protocol_error": "Der Mailserver hat unerwartet geantwortet.",
    "imap_connection_lost": "Die Verbindung zum Mailserver ist abgebrochen.",
    "imap_command_forbidden": "Ein nicht erlaubter Postfachbefehl wurde blockiert.",
    "imap_mailbox_changed": "Das Postfach wurde auf dem Server neu aufgebaut – bitte erneut abrufen.",
    "fetch_disabled": "Auf diesem PC ist der Abruf ausgeschaltet (nur ein PC ruft das Postfach ab).",
    "handover_passive": "Der andere PC hat die Daten (Übergabe) – hier wird nicht abgerufen.",
}
"""Error codes of a failed fetch (``SourceError``) as text for the inbox."""

INTERNAL_ERROR = "Interner Fehler – Details in daten/agent.log."
"""Shown for unexpected errors; the agent runs without a window, so the details are in the log file."""

TRIGGERS: dict[str, str] = {
    "manuell": "per Knopf",
    "zeitplan": "automatisch",
    "kommandozeile": "per Kommandozeile",
}
"""How a fetch was started, as shown next to "Letzter Abruf"."""


def notice_text(code: str | None, count: int | None = None) -> str | None:
    """Returns the confirmation line for ``?hinweis=<code>``.

    Args:
        code: The notice code from the address, or ``None``.
        count: The number from ``?anzahl=``; it replaces ``{anzahl}`` in the text.

    Returns:
        The German text, or ``None`` for a missing or unknown code.
    """
    notice = NOTICES.get(code or "")
    if notice is not None and count is not None:
        return notice.format(anzahl=count)
    return notice


def error_text(code: str | None) -> str | None:
    """Returns the readable text for the error code of a background job.

    The stored code never contains mail content, so it is safe to show and to log.

    Args:
        code: A ``SourceError`` code, ``intern:<Type>`` for unexpected errors, or ``None``.

    Returns:
        The German text, or ``None`` when there was no error.
    """
    if code is None:
        return None
    if code.startswith("intern:"):
        return INTERNAL_ERROR
    return SOURCE_ERRORS.get(code, f"Abruf fehlgeschlagen ({code}).")
