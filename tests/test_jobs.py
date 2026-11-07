"""Background jobs and the daily fetch schedule."""

from datetime import date, datetime, time

import pytest

from mail_agent.config import FetchSettings, Settings
from mail_agent.jobs import STATE_LAST_SLOT, JobRunner, Scheduler, due_slot, next_slot
from mail_agent.sample_data import write_samples
from mail_agent.service import MailAgentService

NINE = (time(9, 0),)


@pytest.mark.parametrize(
    ("now", "last", "expected"),
    [
        # 08:00, never fetched by the schedule: yesterday's 09:00 was missed -> catch up now
        (datetime(2026, 11, 5, 8, 0), None, datetime(2026, 11, 4, 9, 0)),
        # 08:00, yesterday's fetch happened: wait for 09:00
        (datetime(2026, 11, 5, 8, 0), datetime(2026, 11, 4, 9, 0), None),
        # 09:00 sharp: due
        (datetime(2026, 11, 5, 9, 0), datetime(2026, 11, 4, 9, 0), datetime(2026, 11, 5, 9, 0)),
        # PC was off from Friday to Monday 10:30: one catch-up for the latest time, not three
        (datetime(2026, 11, 9, 10, 30), datetime(2026, 11, 6, 9, 0), datetime(2026, 11, 9, 9, 0)),
        # already done today
        (datetime(2026, 11, 5, 17, 0), datetime(2026, 11, 5, 9, 0), None),
    ],
)
def test_due_slot(now: datetime, last: datetime | None, expected: datetime | None) -> None:
    assert due_slot(now, NINE, last) == expected


def test_due_slot_with_two_times_and_without_schedule() -> None:
    times = (time(9, 0), time(14, 0))
    assert due_slot(datetime(2026, 11, 5, 15, 0), times, datetime(2026, 11, 5, 9, 0)) == datetime(
        2026, 11, 5, 14, 0
    )
    assert due_slot(datetime(2026, 11, 5, 15, 0), (), None) is None


def test_next_slot() -> None:
    assert next_slot(datetime(2026, 11, 5, 8, 0), NINE) == datetime(2026, 11, 5, 9, 0)
    assert next_slot(datetime(2026, 11, 5, 9, 0), NINE) == datetime(2026, 11, 6, 9, 0)
    assert next_slot(datetime(2026, 11, 5, 9, 0), ()) is None


def test_scheduler_catches_up_once_and_remembers(service: MailAgentService, settings: Settings) -> None:
    write_samples(settings.eml_dir)
    service.fetch = FetchSettings(start_date=date(2026, 11, 3), times=NINE)
    now = [datetime(2026, 11, 5, 10, 0)]
    runner = JobRunner(service, background=False)
    scheduler = Scheduler(service, runner, clock=lambda: now[0])

    assert scheduler.next_run() == now[0]  # missed 09:00 -> right now
    assert scheduler.check()
    status = runner.status()
    assert status is not None
    assert (status.trigger, status.imported, status.error) == ("zeitplan", 13, None)
    assert service.repository.state.get(STATE_LAST_SLOT) == "2026-11-05T09:00"
    assert not scheduler.check()  # done for today
    assert scheduler.next_run() == datetime(2026, 11, 6, 9, 0)
    run = service.repository.runs.last()
    assert run is not None
    assert run.trigger == "zeitplan"


def test_failed_scheduled_fetch_is_retried_later(service: MailAgentService) -> None:
    service.fetch = FetchSettings(times=NINE)  # the .eml folder does not exist -> fetch fails
    now = [datetime(2026, 11, 5, 9, 0)]
    runner = JobRunner(service, background=False)
    scheduler = Scheduler(service, runner, clock=lambda: now[0])
    assert scheduler.check()
    assert service.repository.state.get(STATE_LAST_SLOT) is None
    now[0] = datetime(2026, 11, 5, 9, 5)
    assert not scheduler.check()  # waits 15 minutes
    now[0] = datetime(2026, 11, 5, 9, 16)
    assert scheduler.check()


def test_scheduler_is_off_without_times_or_on_other_pcs(service: MailAgentService) -> None:
    runner = JobRunner(service, background=False)
    clock = lambda: datetime(2026, 11, 5, 10, 0)  # noqa: E731
    service.fetch = FetchSettings(times=())
    assert not Scheduler(service, runner, clock=clock).check()
    service.fetch = FetchSettings(enabled=False, times=NINE)
    scheduler = Scheduler(service, runner, clock=clock)
    assert not scheduler.check()
    assert scheduler.next_run() is None


def test_background_job_reports_progress_and_result(service: MailAgentService, settings: Settings) -> None:
    write_samples(settings.eml_dir)
    runner = JobRunner(service)
    assert runner.start("abruf", "manuell")
    status = runner.wait()
    assert status is not None
    assert not status.running
    assert (status.phase, status.total, status.done, status.imported) == ("fertig", 13, 13, 13)
    assert status.summary.startswith("13 neu eingelesen")
    assert status.as_dict()["phase_text"] == "Fertig"
