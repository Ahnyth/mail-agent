"""Background work that starts and stops together with the web server."""

import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from ..jobs import JobRunner, Scheduler
from ..service import MailAgentService


class BackgroundWork:
    """The fetch schedule, the speech model and the hand-over, bound to the lifetime of the server."""

    def __init__(self, service: MailAgentService, jobs: JobRunner, schedule: Scheduler) -> None:
        """Keeps the parts; nothing starts before :meth:`start`."""
        self._service = service
        self._jobs = jobs
        self._schedule = schedule

    def start(self) -> None:
        """Loads the speech model in the background and starts the schedule.

        The schedule also catches up a fetch that was missed while the PC was off.
        """
        transcriber = self._service.transcriber
        if transcriber is not None:  # the first voice command should not wait for the model
            threading.Thread(target=transcriber.warm_up, name="whisper-laden", daemon=True).start()
        self._schedule.start()

    def stop(self) -> None:
        """Stops the schedule and the running job, then writes pending changes into the hand-over package.

        Without the last step, changes of the final minute would reach the other PC only after the next
        start of this one.
        """
        self._schedule.stop()
        self._jobs.shutdown()
        self._service.handover_tick(busy=self._jobs.running, wait_for_quiet=False)


def lifespan_for(work: BackgroundWork) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Returns a FastAPI lifespan that runs ``work`` while the server is up."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Starts the work before the first request and stops it after the last one, even after an error."""
        work.start()
        try:
            yield
        finally:
            work.stop()

    return lifespan
