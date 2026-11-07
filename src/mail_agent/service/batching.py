"""Portions and resume points: how long runs are cut into pieces that survive an interruption."""

import threading
from collections.abc import Iterator, Sequence
from datetime import date

from ..models import MailMessage
from ..senders import identity_address
from ..sources import FetchCursor
from ..storage import FetchState

BATCH_SIZE = 20
"""Messages read per portion. The position is saved after every portion, so an interrupted fetch (PC
switched off, connection lost) continues where it stopped."""


def batches[Item](items: Sequence[Item]) -> Iterator[Sequence[Item]]:
    """Cuts ``items`` into portions of :data:`BATCH_SIZE` (read at call time, so tests can lower it)."""
    size = BATCH_SIZE
    for start in range(0, len(items), size):
        yield items[start : start + size]


def stop_requested(stop: threading.Event | None) -> bool:
    """Checks whether the run was asked to stop, e.g. because the agent is shutting down."""
    return stop is not None and stop.is_set()


def resume_cursor(state: FetchState | None, start: date | None) -> FetchCursor | None:
    """Returns where reading continues, or ``None`` to read from the start date again.

    An earlier start date than the stored one means: look at the older mails again.

    Args:
        state: The stored position of the source, if any.
        start: The current start date of the fetch.

    Returns:
        The cursor after the last message read, or ``None``.
    """
    if state is None or state.validity is None:
        return None
    moved_earlier = start is not None and state.start_date is not None and start < state.start_date
    return None if moved_earlier else FetchCursor(state.validity, state.last_uid)


def first_per_sender[Item](
    pending: list[tuple[Item, MailMessage]],
) -> Iterator[list[tuple[Item, MailMessage]]]:
    """Groups messages into rounds: the first mail of every sender, then the second one, and so on.

    Mails of one sender are therefore never analysed side by side, so the later ones already see how the
    first one was classified (consistency per sender).
    """
    rounds: list[list[tuple[Item, MailMessage]]] = []
    seen: dict[str, int] = {}
    for item in pending:
        identity = identity_address(item[1].sender_address, item[1].reply_to)
        index = seen.get(identity, 0)
        seen[identity] = index + 1
        if index == len(rounds):
            rounds.append([])
        rounds[index].append(item)
    yield from rounds
