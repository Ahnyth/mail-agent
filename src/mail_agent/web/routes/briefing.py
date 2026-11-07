"""The morning briefing and reading texts aloud with the German Windows voice or Piper."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ...voice import MAX_SPEECH_CHARS, VoiceUnavailableError
from ..context import Context

router = APIRouter()

WAV_TYPE = "audio/wav"


@router.get("/briefing", response_class=HTMLResponse)
def briefing_page(request: Request, context: Context) -> Response:
    """Shows today's briefing with the text that can be read aloud."""
    briefing = context.briefing()
    data = {"b": briefing, "speech": briefing.speech_text(), "has_voice": context.service.speaker is not None}
    return context.render(request, "briefing.html", data)


@router.get("/briefing/audio")
def briefing_audio(context: Context) -> Response:
    """Returns the briefing spoken by the optional Piper voice."""
    speaker = context.service.speaker
    if speaker is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Keine Piper-Stimme eingerichtet")
    try:
        audio = speaker.synthesize(context.briefing().speech_text())
    except VoiceUnavailableError:
        raise HTTPException(
            HTTPStatus.SERVICE_UNAVAILABLE, "Piper ist nicht installiert (uv sync --extra stimme)"
        ) from None
    return Response(audio, media_type=WAV_TYPE)


@router.post("/vorlesen")
def read_aloud(context: Context, text: Annotated[str, Form(max_length=MAX_SPEECH_CHARS)] = "") -> Response:
    """Returns ``text`` spoken by the German Windows voice; offline, nothing is stored."""
    voice = context.service.windows_voice
    if voice is None:
        return JSONResponse({"error": "no_windows_voice"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)
    if not text.strip():
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Kein Text")
    try:
        audio = voice.synthesize(text)
    except VoiceUnavailableError as exc:
        return JSONResponse({"error": exc.code}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)
    return Response(audio, media_type=WAV_TYPE)
