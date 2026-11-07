"""The morning briefing and reading texts aloud with the German Windows voice."""

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
    return context.render(request, "briefing.html", {"b": briefing, "speech": briefing.speech_text()})


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
