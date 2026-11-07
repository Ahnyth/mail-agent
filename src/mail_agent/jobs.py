"""Background jobs (fetch, AI catch-up) with progress, and the daily fetch schedule.

Only one job runs at a time. The web page starts a job and asks for its progress every few
seconds instead of waiting for minutes on one request. The schedule fetches at fixed local times
while the agent runs; a time missed because the PC was off is caught up on the next start.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from .security.redaction import get_logger
from .service import ImportReport, MailAgentService
from .service.execution import KIND_AI, KIND_FETCH, KIND_REANALYZE
from .sources import SourceError

_log = get_logger("jobs")

KIND_LABELS = {KIND_FETCH: "Abruf", KIND_AI: "KI-Auswertung", KIND_REANALYZE: "Neuauswertung"}
"""German names of the job kinds, as the web page and the icon next to the clock show them."""
STATE_LAST_SLOT = "zeitplan_letzter_termin"
"""Key in ``app_state`` of the last scheduled time that was fetched successfully."""
RETRY_AFTER = timedelta(minutes=15)
"""A scheduled fetch that failed (no network, server down) is tried again after this time."""
SHUTDOWN_WAIT_SECONDS = 5.0
"""How long stopping the agent waits for a job to finish its current portion; its position is saved anyway."""
WAIT_SECONDS = 30.0
"""Upper limit for :meth:`JobRunner.wait` (tests and tools), so nothing ever waits without end."""
CHECK_INTERVAL_SECONDS = 30.0
"""How often the schedule looks at the clock: a fetch starts at most half a minute late."""

PHASES = {
    "start": "Startet …",
    "suchen": "Verbinde mit dem Postfach und suche neue Nachrichten …",
    "lesen": "Lese Nachrichten …",
    "auswerten": "Werte aus …",
    "gesendet": "Prüfe den Gesendet-Ordner auf Antworten …",
    "fertig": "Fertig",
    "fehler": "Abgebrochen",
}
"""German texts of the phases a job reports, shown under the progress bar."""


@dataclass(frozen=True)
class JobStatus:
    """The state of the current or last job, as the web page polls it."""

    kind: str
    trigger: str
    running: bool
    phase: str
    started_at: datetime
    finished_at: datetime | None = None
    total: int = 0
    done: int = 0
    imported: int = 0
    duplicates: int = 0
    failed: int = 0
    ai_pending: int = 0
    error: str | None = None
    """Source error code (e.g. ``imap_login_failed``) or ``intern:<Type>``."""
    summary: str = ""

    def as_dict(self) -> dict[str, object]:
        """Returns the status as JSON-ready values, with the German text of the phase."""
        return {
            "kind": self.kind,
            "trigger": self.trigger,
            "running": self.running,
            "phase": self.phase,
            "phase_text": PHASES.get(self.phase, self.phase),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds") if self.finished_at else None,
            "total": self.total,
            "done": self.done,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "failed": self.failed,
            "ai_pending": self.ai_pending,
            "error": self.error,
            "summary": self.summary,
        }


def running_text(status: JobStatus | None) -> str | None:
    """Returns what is running right now, e.g. ``"Abruf (12 von 40)"``; ``None`` when nothing runs.

    Used before the agent is stopped, so nobody ends a fetch by accident without being asked.
    """
    if status is None or not status.running:
        return None
    label = KIND_LABELS.get(status.kind, status.kind)
    return f"{label} ({status.done} von {status.total})" if status.total else label


Done = Callable[[JobStatus], None]
"""Called with the final status when a job has ended, successfully or not."""


class JobRunner:
    """Runs one job at a time in a background thread (or inline with ``background=False``, for tests)."""

    def __init__(self, service: MailAgentService, *, background: bool = True) -> None:
        """Runs the jobs of ``service``; ``background=False`` runs them inline (tests)."""
        self._service = service
        self._background = background
        self._lock = threading.Lock()
        self._status: JobStatus | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def status(self) -> JobStatus | None:
        """Returns the status of the current or last job; ``None`` before the first job."""
        with self._lock:
            return self._status

    @property
    def running(self) -> bool:
        """Tells whether a job is running right now."""
        status = self.status()
        return status is not None and status.running

    def start(
        self, kind: str, trigger: str, on_done: Done | None = None, ids: list[int] | None = None
    ) -> bool:
        """Starts a job; ``False`` if one is already running. ``ids`` are for ``KIND_REANALYZE``."""
        with self._lock:
            if self._status is not None and self._status.running:
                return False
            self._status = JobStatus(kind, trigger, running=True, phase="start", started_at=datetime.now())
            self._stop.clear()
        if not self._background:
            self._run(kind, trigger, on_done, ids)
            return True
        self._thread = threading.Thread(
            target=self._run, args=(kind, trigger, on_done, ids), name=f"job-{kind}", daemon=True
        )
        self._thread.start()
        return True

    def shutdown(self, timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
        """Asks a running job to stop after the current portion (its position is saved)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def wait(self, timeout: float = WAIT_SECONDS) -> JobStatus | None:
        """Waits at most ``timeout`` seconds for the background job and returns the status then."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.status()

    def _update(self, **changes: object) -> JobStatus:
        """Replaces fields of the status under the lock and returns the new status."""
        with self._lock:
            assert self._status is not None  # noqa: S101 – set by start()
            self._status = replace(self._status, **changes)  # type: ignore[arg-type]
            return self._status

    def _progress(self, phase: str, report: ImportReport) -> None:
        """Copies the counters of ``report`` into the status (called by the service after every step)."""
        self._update(
            phase=phase,
            total=report.found,
            done=report.processed,
            imported=report.imported,
            duplicates=report.duplicates,
            failed=len(report.failed),
            ai_pending=report.ai_pending,
        )

    def _run(self, kind: str, trigger: str, on_done: Done | None, ids: list[int] | None = None) -> None:
        """Runs the job, records how it ended and hands the final status to ``on_done``."""
        try:
            report = self._service.run(kind, trigger, self._progress, self._stop, ids)
        except SourceError as exc:
            status = self._failed(exc.code)
        except Exception as exc:
            _log.exception("Hintergrundauftrag %s fehlgeschlagen", kind)
            status = self._failed(f"intern:{type(exc).__name__}")
        else:
            self._progress("fertig", report)
            status = self._update(running=False, summary=report.text, finished_at=datetime.now())
        if on_done is not None:
            on_done(status)

    def _failed(self, error: str) -> JobStatus:
        """Marks the job as ended with ``error`` and returns the final status."""
        return self._update(running=False, phase="fehler", error=error, finished_at=datetime.now())


def due_slot(now: datetime, times: tuple[time, ...], last_slot: datetime | None) -> datetime | None:
    """Returns the latest scheduled time up to ``now`` if it has not been handled yet.

    Looks back to yesterday, so a fetch missed because the PC was off is caught up on the next start.
    """
    if not times:
        return None
    today = [slot for slot in (datetime.combine(now.date(), t) for t in times) if slot <= now]
    latest = today[-1] if today else datetime.combine(now.date() - timedelta(days=1), times[-1])
    if last_slot is not None and last_slot >= latest:
        return None
    return latest


def next_slot(now: datetime, times: tuple[time, ...]) -> datetime | None:
    """Returns the next scheduled time after ``now`` (today or tomorrow); ``None`` without times."""
    for day in (now.date(), now.date() + timedelta(days=1)):
        for t in times:
            candidate = datetime.combine(day, t)
            if candidate > now:
                return candidate
    return None


class Scheduler:
    """Starts a fetch at the configured times while the agent runs (only on the fetching PC)."""

    def __init__(
        self,
        service: MailAgentService,
        runner: JobRunner,
        *,
        clock: Callable[[], datetime] = datetime.now,
        interval_seconds: float = CHECK_INTERVAL_SECONDS,
    ) -> None:
        """Plans the fetches of ``service`` and starts them through ``runner``.

        Args:
            service: Knows the times and whether this PC may fetch.
            runner: Runs the fetch in the background.
            clock: Returns the current local time (replaceable in tests).
            interval_seconds: Pause between two looks at the clock.
        """
        self._service = service
        self._runner = runner
        self._clock = clock
        self._interval = interval_seconds
        self._retry_at: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        """Tells whether the schedule fetches: times are set and this PC may fetch.

        With a hand-over, only the PC that holds the data may fetch.
        """
        return bool(self._service.fetch.times) and self._service.may_fetch()

    def last_slot(self) -> datetime | None:
        """Returns the last scheduled time that was fetched successfully."""
        value = self._service.repository.state.get(STATE_LAST_SLOT)
        return datetime.fromisoformat(value) if value else None

    def next_run(self) -> datetime | None:
        """Returns when the schedule fetches next; ``None`` while it is not active."""
        if not self.active:
            return None
        now = self._clock()
        if due_slot(now, self._service.fetch.times, self.last_slot()) is not None:
            return self._retry_at or now  # due right now (or after a failed attempt)
        return next_slot(now, self._service.fetch.times)

    def check(self) -> bool:
        """Runs one scheduler tick; ``True`` if a fetch was started."""
        slot = self._slot_to_fetch()
        if slot is None:
            return False
        started = self._runner.start(
            KIND_FETCH, "zeitplan", on_done=lambda status: self._record_result(slot, status)
        )
        if started:
            _log.info("Automatischer Abruf für %s gestartet", slot.strftime("%d.%m. %H:%M"))
        return started

    def start(self) -> None:
        """Starts the background loop once (only with a schedule or a hand-over)."""
        # Runs on both PCs when there is a schedule or a hand-over: the other PC may take over at any time.
        if self._thread is not None or not (self._service.fetch.times or self._service.handover_configured):
            return
        self._thread = threading.Thread(target=self._loop, name="zeitplan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ends the background loop at its next pause."""
        self._stop.set()

    def _slot_to_fetch(self) -> datetime | None:
        """Returns the scheduled time to fetch now; ``None`` if inactive, nothing is due or a retry waits."""
        if not self.active:
            return None
        now = self._clock()
        slot = due_slot(now, self._service.fetch.times, self.last_slot())
        if slot is None or (self._retry_at is not None and now < self._retry_at):
            return None
        return slot

    def _record_result(self, slot: datetime, status: JobStatus) -> None:
        """Marks ``slot`` as handled after a successful fetch; after a failure a retry is planned."""
        if status.error is None:
            self._service.repository.state.set(STATE_LAST_SLOT, slot.isoformat(timespec="minutes"))
            self._retry_at = None
        else:
            self._retry_at = self._clock() + RETRY_AFTER
            _log.warning("Automatischer Abruf fehlgeschlagen (%s) – neuer Versuch später", status.error)

    def _loop(self) -> None:
        """Checks the schedule and the hand-over until :meth:`stop`; one failed check never ends the loop."""
        while not self._stop.is_set():
            try:
                self.check()
                self._service.handover_tick(busy=self._runner.running)
            except Exception:
                _log.exception("Zeitplan: Prüfung fehlgeschlagen")
            self._stop.wait(self._interval)
