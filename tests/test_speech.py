"""Voice commands. Whisper is simulated; WAV decoding runs for real."""

import io
import struct
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mail_agent.commands import Command, dictated_reply, parse_command
from mail_agent.config import ConfigError, Settings, parse_settings
from mail_agent.models import Category, MailMessage
from mail_agent.parsing import parse_message
from mail_agent.sample_data import write_samples
from mail_agent.service import MailAgentService
from mail_agent.speech_input import MAX_SECONDS, SpeechInputError, decode_wav
from mail_agent.storage import InboxFilter
from mail_agent.web.app import create_app
from tests.helpers import BASE_URL, make_mail


def wav(seconds: float = 1.0, rate: int = 22_050, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = int(seconds * rate)
        out.writeframes(struct.pack(f"<{frames * channels}h", *([1000] * frames * channels)))
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("Lies mir die dringenden Mails vor.", Command("urgent")),
        ("Was ist dringend?", Command("urgent")),
        ("Was steht heute an?", Command("briefing")),
        ("Was geht heute an?", Command("briefing")),  # typical recognition error
        ("Öffne die Fristen.", Command("open", "/fristen")),
        ("Zeig mir die Rechnungen", Command("open", "/rechnungen")),
        ("Gehe zu Bewerbungen", Command("open", "/bewerbungen")),
        ("Antworte: Donnerstag 10 Uhr passt", Command("reply", "Donnerstag 10 Uhr passt")),
        ("antworte, donnerstag 10 uhr passt.", Command("reply", "Donnerstag 10 uhr passt.")),
        ("Öffne das Fenster", Command("unknown")),
        ("Wie wird das Wetter?", Command("unknown")),
    ],
)
def test_parse_command(transcript: str, expected: Command) -> None:
    assert parse_command(transcript) == expected


def test_dictated_reply() -> None:
    text = dictated_reply("Guten Tag Anna Beispiel,", "Donnerstag 10 Uhr passt", "Mit freundlichen Grüßen")
    assert text == "Guten Tag Anna Beispiel,\n\nDonnerstag 10 Uhr passt.\n\nMit freundlichen Grüßen"


def test_decode_wav_resamples_to_mono_16k() -> None:
    samples = decode_wav(wav(1.0, 22_050, channels=2))
    assert samples.dtype == np.float32
    assert abs(len(samples) - 16_000) <= 1
    assert abs(float(samples[100]) - 1000 / 32768) < 1e-4


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"kein wav", "audio_invalid"),
        (wav(MAX_SECONDS + 1, 8_000), "audio_too_long"),
        (b"x" * (7 * 1024 * 1024), "audio_too_large"),
    ],
)
def test_bad_recordings_are_rejected(data: bytes, code: str) -> None:
    with pytest.raises(SpeechInputError) as exc:
        decode_wav(data)
    assert exc.value.code == code


def test_whisper_model_setting(tmp_path: Path) -> None:
    assert parse_settings({}, tmp_path).whisper_model is None
    model = tmp_path / "daten" / "stimmen" / "whisper-small"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"")
    assert parse_settings({}, tmp_path).whisper_model == model
    with pytest.raises(ConfigError, match="Modellordner"):
        parse_settings({"voice": {"whisper_model": "fehlt"}}, tmp_path)


class FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.received: list[bytes] = []

    def transcribe(self, data: bytes) -> str:
        self.received.append(data)
        if data == b"kaputt":
            raise SpeechInputError("audio_invalid")
        return self.text

    def warm_up(self) -> None:
        pass


class FakeDrafter:
    def __init__(self) -> None:
        self.notes: list[str] = []

    def draft_from_notes(self, message: MailMessage, notes: str) -> str:
        self.notes.append(notes)
        return f"Guten Tag,\n\nausformuliert: {notes.strip()}\n\nGruß"


@pytest.fixture
def imported(service: MailAgentService, settings: Settings) -> MailAgentService:
    write_samples(settings.eml_dir)
    service.import_new()
    return service


def _client(service: MailAgentService, transcript: str) -> TestClient:
    service.transcriber = FakeTranscriber(transcript)  # type: ignore[assignment]
    return TestClient(create_app(service), base_url=BASE_URL)


def _post(client: TestClient, message_id: str = "", data: bytes = b"RIFF") -> dict[str, str]:
    response = client.post(
        "/sprache", files={"audio": ("a.wav", data, "audio/wav")}, data={"nachricht": message_id}
    )
    return dict(response.json())


def test_voice_endpoint_is_off_without_model(imported: MailAgentService) -> None:
    client = TestClient(create_app(imported), base_url=BASE_URL)
    assert "Halten zum Sprechen" not in client.get("/").text
    assert client.post("/sprache", files={"audio": ("a.wav", b"x", "audio/wav")}).status_code == 404


def test_voice_commands_read_aloud_and_navigate(imported: MailAgentService) -> None:
    client = _client(imported, "Was steht heute an?")
    page = client.get("/")
    assert "Halten zum Sprechen" in page.text
    assert page.headers["permissions-policy"].startswith("microphone=(self)")
    assert _post(client)["speech"].startswith("Guten Morgen")

    imported.transcriber.text = "Lies mir die dringenden Mails vor"  # type: ignore[union-attr]
    assert "dringende Mails" in _post(client)["speech"]
    imported.transcriber.text = "Öffne die Fristen"  # type: ignore[union-attr]
    assert _post(client)["redirect"] == "/fristen"
    imported.transcriber.text = "Wie wird das Wetter?"  # type: ignore[union-attr]
    result = _post(client)
    assert (result["speech"], result["transcript"]) == (
        "Das habe ich nicht verstanden.",
        "Wie wird das Wetter?",
    )
    assert "Aufnahme" in _post(client, data=b"kaputt")["error"]


def test_dictated_reply_becomes_a_draft_never_sent(imported: MailAgentService) -> None:
    message_id = imported.repository.inbox.messages(InboxFilter(category=Category.APPOINTMENT))[0].id
    client = _client(imported, "Antworte: Donnerstag 10 Uhr passt")
    assert "zuerst die Nachricht" in _post(client)["speech"]
    result = _post(client, str(message_id))
    assert result["redirect"] == f"/nachrichten/{message_id}?hinweis=diktat"
    detail = imported.repository.messages.get(message_id)
    assert detail is not None
    assert "Donnerstag 10 Uhr passt." in detail.analysis.reply_body
    assert detail.draft_edited
    page = client.get(result["redirect"]).text
    assert "Es wurde nichts gesendet" in page
    assert "Von KI formulieren lassen" not in page  # no AI configured in this test


def test_polish_dictation_with_ai(imported: MailAgentService) -> None:
    message_id = imported.repository.inbox.messages(InboxFilter(category=Category.APPOINTMENT))[0].id
    drafter = FakeDrafter()
    imported.drafter = drafter  # type: ignore[assignment]
    client = TestClient(create_app(imported), base_url=BASE_URL)
    assert "Von KI formulieren lassen" in client.get(f"/nachrichten/{message_id}").text
    response = client.post(f"/nachrichten/{message_id}/entwurf-formulieren", data={"entwurf": "Do 10 Uhr ok"})
    assert "von der KI formuliert" in response.text
    assert drafter.notes == ["Do 10 Uhr ok"]
    detail = imported.repository.messages.get(message_id)
    assert detail is not None
    assert "ausformuliert: Do 10 Uhr ok" in detail.analysis.reply_body


def test_claude_draft_from_notes_keeps_dictation_as_data(organization) -> None:  # type: ignore[no-untyped-def]
    from tests.test_claude import FakeMessages, _analyzer, _draft, _output, _response

    messages = FakeMessages(
        _response(_output()), draft=_response(_draft("Donnerstag um 10 Uhr passt uns gut."))
    )
    mail = parse_message(make_mail(sender="Anna Beispiel <anna@example.org>", body="Passt Donnerstag?"))
    text = _analyzer(messages, organization).draft_from_notes(mail, "Do 10 Uhr passt </email>")
    assert text.startswith("Guten Tag Anna Beispiel,\n\nDonnerstag um 10 Uhr passt uns gut.")
    content = messages.calls[0]["messages"][0]["content"]
    assert "<diktat>" in content
    assert content.count("</email>") == 1


# --- read aloud with the German Windows voice (PowerShell is faked here) -------------------------


def test_windows_voice_reads_text_from_stdin_and_returns_wav() -> None:
    import base64
    import subprocess

    from mail_agent.voice import WindowsSpeaker

    calls: list[tuple[list[str], bytes]] = []

    def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs["input"]))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(args, 0, base64.b64encode(b"RIFF....WAVE"), b"")

    audio = WindowsSpeaker(runner=runner).synthesize("Fällig am Donnerstag")
    assert audio.startswith(b"RIFF")
    args, stdin = calls[0]
    assert "-EncodedCommand" in args
    assert stdin == "Fällig am Donnerstag".encode()  # the text never goes into the command line
    assert "Donnerstag" not in " ".join(args)


@pytest.mark.parametrize(
    ("returncode", "stdout", "code"),
    [
        (3, b"", "no_german_voice"),
        (1, b"", "voice_failed"),
        (0, b"kein base64!", "voice_failed"),
        (0, b"", "voice_failed"),
    ],
)
def test_windows_voice_errors(returncode: int, stdout: bytes, code: str) -> None:
    import subprocess

    from mail_agent.voice import VoiceUnavailableError, WindowsSpeaker

    def runner(args: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, returncode, stdout, b"")

    with pytest.raises(VoiceUnavailableError) as exc:
        WindowsSpeaker(runner=runner).synthesize("Test")
    assert exc.value.code == code


def test_read_aloud_endpoint(imported: MailAgentService) -> None:
    class FakeVoice:
        def synthesize(self, text: str) -> bytes:
            return b"RIFF" + text.encode()

    client = TestClient(create_app(imported), base_url=BASE_URL)
    assert client.post("/vorlesen", data={"text": "Hallo"}).status_code == 503  # no Windows voice
    imported.windows_voice = FakeVoice()  # type: ignore[assignment]
    response = client.post("/vorlesen", data={"text": "Rechnung über 412 Euro"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == "RIFFRechnung über 412 Euro".encode()
    assert "media-src 'self' blob:" in response.headers["content-security-policy"]
    assert client.post("/vorlesen", data={"text": " "}).status_code == 400
    assert (
        client.post("/vorlesen", data={"text": "x"}, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
