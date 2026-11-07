"""Local speech recognition with faster-whisper (optional extra ``sprache``, MIT).

The browser sends a short WAV recording (push-to-talk). It is decoded here in memory, transcribed
on the CPU and discarded – audio is never written to disk or logged.

PyAV (used by faster-whisper only to decode audio files) may be blocked by Windows application
control. It is not needed: WAV is decoded with the standard library and the samples are passed to
the model as an array.
"""

import importlib
import io
import sys
import threading
import types
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .security.redaction import get_logger

SAMPLE_RATE = 16_000
"""Whisper expects 16 kHz mono samples; every recording is converted to this rate."""
MAX_SECONDS = 30
"""Longer recordings are refused; Whisper works on windows of 30 seconds, and a command is far shorter."""
MAX_WAV_BYTES = 6 * 1024 * 1024
"""Upper bound for an upload: 30 seconds of 48 kHz stereo 16-bit WAV are about 5.5 MB."""
MIN_SAMPLES = SAMPLE_RATE // 4
"""Recordings shorter than a quarter of a second hold no word; they are refused instead of guessed."""
SAMPLE_WIDTH_BYTES = 2
"""Only 16-bit PCM is accepted, the format browsers record WAV in."""
MIN_RATE = 8_000
"""The lowest sample rate accepted (telephone quality)."""
MAX_RATE = 96_000
"""The highest sample rate accepted (studio equipment)."""
STEREO = 2
"""Channel count of a stereo recording; mono has 1."""
BEAM_SIZE = 5
"""faster-whisper's default beam search width: a good balance of accuracy and speed on the CPU."""

_INT16_SCALE = 32768.0
"""Divides 16-bit samples into the range -1 to 1 that Whisper expects."""

_log = get_logger("speech_input")

Samples = np.ndarray[Any, np.dtype[np.float32]]
"""Mono audio samples between -1 and 1."""


class SpeechInputError(Exception):
    """Recording unusable or recognition unavailable; ``code`` is a fixed diagnostic code."""

    def __init__(self, code: str) -> None:
        """Keeps ``code`` for the web interface, which shows a German text for it."""
        super().__init__(code)
        self.code = code


def decode_wav(data: bytes) -> Samples:
    """Converts a 16-bit PCM WAV (any rate, mono or stereo) into mono float32 samples at 16 kHz.

    Args:
        data: The WAV file sent by the browser.

    Returns:
        The samples at :data:`SAMPLE_RATE`.

    Raises:
        SpeechInputError: The recording is too large, too long, broken or in an unsupported format.
    """
    if len(data) > MAX_WAV_BYTES:
        raise SpeechInputError("audio_too_large")
    channels, width, rate, frames = _read_wav(data)
    if width != SAMPLE_WIDTH_BYTES or channels not in (1, STEREO) or not MIN_RATE <= rate <= MAX_RATE:
        raise SpeechInputError("audio_format")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / _INT16_SCALE
    if channels == STEREO:
        samples = samples.reshape(-1, STEREO).mean(axis=1)
    if len(samples) / rate > MAX_SECONDS:
        raise SpeechInputError("audio_too_long")
    return _resample(samples, rate).astype(np.float32)


def _read_wav(data: bytes) -> tuple[int, int, int, bytes]:
    """Returns channel count, sample width, sample rate and the raw frames of a WAV file.

    Raises:
        SpeechInputError: The data is no readable WAV file (``audio_invalid``).
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            return (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.readframes(wav.getnframes()),
            )
    except (wave.Error, EOFError):
        raise SpeechInputError("audio_invalid") from None


def _resample(samples: Samples, rate: int) -> Samples:
    """Converts ``samples`` from ``rate`` to :data:`SAMPLE_RATE` by linear interpolation."""
    if rate == SAMPLE_RATE or not len(samples):
        return samples
    target = np.arange(0, len(samples), rate / SAMPLE_RATE)
    resampled: Samples = np.interp(target, np.arange(len(samples)), samples).astype(np.float32)
    return resampled


def _import_whisper_model() -> Any:
    """Returns faster-whisper's model class; a blocked PyAV is replaced by an empty stand-in.

    Raises:
        SpeechInputError: faster-whisper is not installed (``whisper_missing``).
    """
    try:
        importlib.import_module("av")
    except ImportError:
        # Blocked or missing PyAV: faster-whisper only uses it to decode files, which we do ourselves.
        sys.modules.setdefault("av", types.ModuleType("av"))
    try:
        return importlib.import_module("faster_whisper").WhisperModel
    except ImportError:
        raise SpeechInputError("whisper_missing") from None


class WhisperTranscriber:
    """Speech recognition with a local Whisper model; the model is loaded on first use."""

    def __init__(self, model_dir: Path) -> None:
        """Recognises with the converted model in ``model_dir``."""
        self._model_dir = model_dir
        self._model: Any = None
        self._lock = threading.Lock()  # one recognition at a time; the model is not shared safely

    def warm_up(self) -> None:
        """Loads the model ahead of the first command (takes several seconds); errors are only logged."""
        try:
            with self._lock:
                self._load()
        except (SpeechInputError, OSError, RuntimeError) as exc:
            _log.warning("Whisper-Modell konnte nicht geladen werden: %s", type(exc).__name__)

    def transcribe(self, wav_data: bytes) -> str:
        """Returns the German text spoken in a WAV recording.

        Args:
            wav_data: The WAV file sent by the browser.

        Returns:
            The recognised text; empty if nothing was understood.

        Raises:
            SpeechInputError: The recording is unusable or faster-whisper is missing.
        """
        samples = decode_wav(wav_data)
        if len(samples) < MIN_SAMPLES:
            raise SpeechInputError("audio_too_short")
        with self._lock:
            model = self._load()
            segments, _ = model.transcribe(samples, language="de", beam_size=BEAM_SIZE, vad_filter=False)
            return " ".join(segment.text.strip() for segment in segments).strip()

    def _load(self) -> Any:
        """Returns the model, loading it on the first call; the caller holds the lock."""
        if self._model is None:
            whisper_model = _import_whisper_model()
            self._model = whisper_model(str(self._model_dir), device="cpu", compute_type="int8")
            _log.info("Whisper-Modell geladen: %s", self._model_dir.name)
        return self._model
