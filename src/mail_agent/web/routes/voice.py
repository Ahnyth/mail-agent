"""Push-to-talk: the recording is transcribed locally and answered by a small local command set."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ...analysis.rules import salutation
from ...commands import Command, dictated_reply, parse_command
from ...security.redaction import get_logger
from ...speech_input import MAX_WAV_BYTES, SpeechInputError
from ..context import Context, WebContext
from ..texts import SPEECH_ERRORS

router = APIRouter()
_log = get_logger("web")

MAX_URGENT_READ = 5
"""More than five subjects in a row are hard to follow when read aloud."""
NOT_UNDERSTOOD = "Das habe ich nicht verstanden."


@router.post("/sprache")
async def voice_command(
    context: Context, audio: Annotated[UploadFile, File()], nachricht: Annotated[str, Form()] = ""
) -> JSONResponse:
    """Transcribes a recording and runs the command in it; the audio is never stored."""
    transcriber = context.service.transcriber
    if transcriber is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Spracheingabe nicht eingerichtet")
    data = await audio.read(MAX_WAV_BYTES + 1)  # one byte more reveals a recording that is too large
    try:
        transcript = transcriber.transcribe(data)
    except SpeechInputError as exc:
        error = SPEECH_ERRORS.get(exc.code, "Aufnahme nicht verwendbar.")
        return JSONResponse({"error": error}, status_code=HTTPStatus.BAD_REQUEST)
    del data
    command = parse_command(transcript)
    _log.info("Sprachbefehl: %s", command.kind)  # never the transcript: it may contain names
    answer = VoiceCommands(context, nachricht).answer(command)
    return JSONResponse({"transcript": transcript, **answer})


class VoiceCommands:
    """Answers a recognised command with a text to read aloud, a page to open or a new draft."""

    def __init__(self, context: WebContext, message_field: str) -> None:
        """Prepares the answers; ``message_field`` is the id of the message open in the browser, if any."""
        self._context = context
        self._message_field = message_field

    def answer(self, command: Command) -> dict[str, str]:
        """Returns ``speech`` (text to read aloud) or ``redirect`` (page to open) for ``command``."""
        handlers: dict[str, Callable[[Command], dict[str, str]]] = {
            "urgent": self._urgent,
            "briefing": self._briefing,
            "open": self._open,
            "reply": self._reply,
        }
        handler = handlers.get(command.kind)
        return handler(command) if handler else {"speech": NOT_UNDERSTOOD}

    def _urgent(self, _command: Command) -> dict[str, str]:
        """Reads the number of urgent mails and the first subjects."""
        urgent = self._context.briefing().urgent
        if not urgent:
            return {"speech": "Keine dringenden Mails."}
        sentences = " ".join(f"{m.analysis.read_aloud or m.subject}." for m in urgent[:MAX_URGENT_READ])
        return {"speech": f"{len(urgent)} dringende Mails. {sentences}"}

    def _briefing(self, _command: Command) -> dict[str, str]:
        """Reads the whole briefing."""
        return {"speech": self._context.briefing().speech_text()}

    @staticmethod
    def _open(command: Command) -> dict[str, str]:
        """Opens the page named in the command, e.g. "zeige Fristen"."""
        return {"redirect": command.text}

    def _reply(self, command: Command) -> dict[str, str]:
        """Turns the dictated words into a draft for the message that is open."""
        field = self._message_field
        message = self._context.repository.messages.get(int(field)) if field.isdigit() else None
        if message is None:
            return {"speech": "Bitte öffnen Sie zuerst die Nachricht, auf die Sie antworten möchten."}
        if not command.text:
            return {"speech": "Ich habe keinen Antworttext verstanden."}
        signature = self._context.service.organization.signature
        draft = dictated_reply(salutation(message.sender_name), command.text, signature)
        self._context.repository.analyses.save_draft(message.id, draft)
        return {"redirect": f"/nachrichten/{message.id}?hinweis=diktat"}
