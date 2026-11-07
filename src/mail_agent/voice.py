"""Offline speech output: the German voice built into Windows, or Piper (optional extra ``stimme``).

Audio is generated on request on this PC and returned directly as WAV; it is neither stored nor sent
anywhere. Only read-aloud texts without names or health data are spoken.

The browser's own speech output (``speechSynthesis``) turned out to be unreliable in Chrome on
Windows (it accepted the text but never started), so the web page plays this WAV like any other
audio – pause, stop and speed then work the same in every browser.
"""

import base64
import binascii
import io
import subprocess
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .processes import NO_WINDOW
from .security.redaction import get_logger

_log = get_logger("voice")

MAX_SPEECH_CHARS = 5000
"""Longer texts are cut (the web form refuses them); 5000 characters are minutes of speech."""
WINDOWS_VOICE_TIMEOUT = 60
"""Upper bound in seconds for one PowerShell synthesis (every wait has an upper bound)."""
NO_GERMAN_VOICE_EXIT_CODE = 3
"""The exit code of :data:`_WINDOWS_SCRIPT` when Windows has no German voice installed (``exit 3``)."""

_WINDOWS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$text = [Console]::In.ReadToEnd()
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() |
    Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'de-*' } |
    Select-Object -First 1
if (-not $voice) { exit 3 }
$synth.SelectVoice($voice.VoiceInfo.Name)
$stream = New-Object System.IO.MemoryStream
$synth.SetOutputToWaveStream($stream)
$synth.Speak($text)
$synth.Dispose()
[Console]::Out.Write([Convert]::ToBase64String($stream.ToArray()))
"""
"""The PowerShell script: text comes via stdin (never in the command line), the WAV goes back as base64."""
_POWERSHELL_OPTIONS = ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand")
"""No profile, no prompts; the script is passed encoded, so no quoting can break it."""

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
"""Starts a program like ``subprocess.run``; tests pass a fake."""


class VoiceUnavailableError(Exception):
    """Piper or the voice model is missing; ``code`` is a fixed diagnostic code."""

    def __init__(self, code: str) -> None:
        """Keeps ``code`` for the web interface, which shows a German text for it."""
        super().__init__(code)
        self.code = code


class PiperSpeaker:
    """The Piper voice (optional extra ``stimme``); the model is loaded on first use."""

    def __init__(self, model_path: Path) -> None:
        """Speaks with the voice model at ``model_path`` (an ``.onnx`` file)."""
        self._model_path = model_path
        self._voice: Any = None
        self._lock = threading.Lock()  # one synthesis at a time; the model is not thread-safe

    def synthesize(self, text: str) -> bytes:
        """Returns the WAV audio of ``text``.

        Raises:
            VoiceUnavailableError: Piper is not installed (``piper_missing``).
        """
        buffer = io.BytesIO()
        with self._lock:
            voice = self._load()
            with wave.open(buffer, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
        return buffer.getvalue()

    def _load(self) -> Any:
        """Returns the voice, loading it on the first call; the caller holds the lock."""
        if self._voice is None:
            try:
                from piper import PiperVoice  # noqa: PLC0415 - optional extra, loaded only when used
            except ImportError:
                raise VoiceUnavailableError("piper_missing") from None
            self._voice = PiperVoice.load(self._model_path)
            _log.info("Piper-Stimme geladen: %s", self._model_path.name)
        return self._voice


class WindowsSpeaker:
    """The German voice that comes with Windows (SAPI, e.g. "Microsoft Hedda"), offline via PowerShell."""

    def __init__(self, runner: Runner = subprocess.run, command: str = "powershell") -> None:
        """Starts ``command`` with ``runner``; tests pass a fake runner."""
        self._runner = runner
        self._command = command
        self._lock = threading.Lock()  # one PowerShell at a time is plenty for one office PC

    def synthesize(self, text: str) -> bytes:
        """Returns the WAV audio of ``text`` (shortened to :data:`MAX_SPEECH_CHARS`).

        Raises:
            VoiceUnavailableError: PowerShell or a German voice is missing, it took too long or failed.
        """
        completed = self._run_script(text[:MAX_SPEECH_CHARS])
        if completed.returncode == NO_GERMAN_VOICE_EXIT_CODE:
            raise VoiceUnavailableError("no_german_voice")
        if completed.returncode != 0:
            _log.warning("Windows-Stimme fehlgeschlagen (Code %s)", completed.returncode)
            raise VoiceUnavailableError("voice_failed")
        return _wav_from_base64(completed.stdout)

    def _run_script(self, text: str) -> subprocess.CompletedProcess[bytes]:
        """Runs :data:`_WINDOWS_SCRIPT` with ``text`` on stdin, one call at a time."""
        encoded = base64.b64encode(_WINDOWS_SCRIPT.encode("utf-16-le")).decode("ascii")
        try:
            with self._lock:
                return self._runner(
                    [self._command, *_POWERSHELL_OPTIONS, encoded],
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=WINDOWS_VOICE_TIMEOUT,
                    check=False,
                    **NO_WINDOW,
                )
        except FileNotFoundError:
            raise VoiceUnavailableError("powershell_missing") from None
        except subprocess.TimeoutExpired:
            raise VoiceUnavailableError("voice_timeout") from None


def _wav_from_base64(output: bytes) -> bytes:
    """Decodes the script output and checks that it is a WAV file.

    Raises:
        VoiceUnavailableError: The output is no base64 or no WAV (``voice_failed``).
    """
    try:
        audio = base64.b64decode(output.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise VoiceUnavailableError("voice_failed") from None
    if not audio.startswith(b"RIFF"):
        raise VoiceUnavailableError("voice_failed")
    return audio
