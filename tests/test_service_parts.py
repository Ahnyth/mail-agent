"""Building blocks of the service: resume points, portions, rounds per sender and the run log entry."""

from datetime import date

import pytest

from mail_agent.parsing import parse_message
from mail_agent.service import ImportReport, MailAgentService, batching
from mail_agent.service.batching import batches, first_per_sender, resume_cursor
from mail_agent.service.execution import run_result
from mail_agent.sources import FetchCursor, SourceError
from mail_agent.storage import FetchState
from tests.helpers import make_mail


def test_resume_cursor_continues_after_the_last_message() -> None:
    state = FetchState("imap:x", "v1", 42, date(2026, 10, 1))
    assert resume_cursor(state, date(2026, 10, 1)) == FetchCursor("v1", 42)
    # a later start date keeps the position
    assert resume_cursor(state, date(2026, 10, 15)) == FetchCursor("v1", 42)


def test_resume_cursor_starts_again_without_position_or_with_an_earlier_start() -> None:
    assert resume_cursor(None, date(2026, 10, 1)) is None
    assert resume_cursor(FetchState("eml:x", None, 0, None), None) is None  # folders have no position
    state = FetchState("imap:x", "v1", 42, date(2026, 10, 1))
    assert resume_cursor(state, date(2026, 9, 1)) is None  # an earlier start date reads the older mails again


def test_batches_follow_the_batch_size_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    assert [list(b) for b in batches([1, 2, 3, 4, 5])] == [[1, 2], [3, 4], [5]]
    assert list(batches([])) == []


def test_first_per_sender_never_puts_two_mails_of_one_sender_in_a_round() -> None:
    def mail(n: int, sender: str) -> tuple[int, object]:
        return n, parse_message(make_mail(message_id=f"<{n}@x>", sender=sender))

    anna, ben = "Anna <anna@example.org>", "Ben <ben@example.org>"
    pending = [mail(1, anna), mail(2, anna), mail(3, ben), mail(4, anna)]
    rounds = [[n for n, _ in group] for group in first_per_sender(pending)]  # type: ignore[arg-type]
    assert rounds == [[1, 3], [2], [4]]


def test_run_result_keeps_what_the_run_log_needs() -> None:
    report = ImportReport(imported=3, duplicates=1, failed=["eml:a: kaputt"], found=5, ai_pending=2)
    result = run_result(report, "imap_login_failed")
    assert (result.found, result.imported, result.duplicates, result.ai_pending) == (5, 3, 1, 2)
    assert result.failed == ("eml:a: kaputt",)
    assert result.error == "imap_login_failed"


def test_failed_fetch_is_recorded_with_its_error(service: MailAgentService) -> None:
    with pytest.raises(SourceError):
        service.run("abruf", "manuell")  # the .eml folder does not exist
    last = service.repository.runs.last("abruf")
    assert last is not None
    assert last.error == "eml_folder_missing"
    assert last.finished_at is not None
