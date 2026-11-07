"""Presentation helpers: the filters of the inbox list and the progress of a background job."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlencode

from ..jobs import KIND_LABELS, JobStatus
from ..models import Category, Responsible, Status
from ..storage import InboxFilter, MailRepository, MessageSummary
from .texts import error_text

MAX_SEARCH_CHARS = 100
"""Longer search terms are cut; nobody types more, and the database query stays cheap."""
MAX_QUERY_CHARS = 500
"""Upper bound for the ``von`` field that carries a list to the detail page."""


@dataclass(frozen=True)
class ListView:
    """The filters of the inbox list.

    The view travels to the detail page as the form field ``von``, so "Zurück" and "erledigt → nächste Mail"
    stay in the same list.
    """

    category: Category | None = None
    responsible: Responsible | None = None
    status: Status | None = Status.OPEN
    ai_pending: bool = False
    search: str = ""
    advertising: bool = False
    """Show advertising in the general list. Hidden by default: more than half of the mails are
    advertising. A category filter, a search or "KI ausstehend" always shows everything that matches."""

    @classmethod
    def from_values(cls, values: Mapping[str, str | None]) -> "ListView":
        """Reads the filters from the German parameter names; unknown values fall back to the defaults.

        Args:
            values: ``kategorie``, ``zustaendig``, ``status``, ``ki``, ``q`` and ``werbung``; missing keys
                count as not set.

        Returns:
            The filters of the list.
        """
        return cls(
            category=next((c for c in Category if c.value == values.get("kategorie")), None),
            responsible=next((r for r in Responsible if r.value == values.get("zustaendig")), None),
            status=next((s for s in Status if s.value == (values.get("status") or "offen")), None),
            ai_pending=values.get("ki") == "ausstehend",
            search=" ".join((values.get("q") or "").split())[:MAX_SEARCH_CHARS],
            advertising=values.get("werbung") == "zeigen",
        )

    @classmethod
    def from_query(cls, query: str) -> "ListView":
        """Reads the filters from an encoded query string, e.g. the ``von`` field of a form."""
        values = {key: items[-1] for key, items in parse_qs(query[:MAX_QUERY_CHARS]).items()}
        return cls.from_values(values)

    def query(self) -> str:
        """Encodes the filters as a query string for links and hidden form fields."""
        params = {"status": self.status.value if self.status else "alle"}
        if self.category:
            params["kategorie"] = self.category.value
        if self.responsible:
            params["zustaendig"] = self.responsible.value
        if self.ai_pending:
            params["ki"] = "ausstehend"
        if self.search:
            params["q"] = self.search
        if self.advertising:
            params["werbung"] = "zeigen"
        return urlencode(params)

    @property
    def hides_advertising(self) -> bool:
        """Whether advertising is left out: only in the general list without any filter."""
        return not (self.advertising or self.category or self.search or self.ai_pending)

    @property
    def shows_advertising_on_request(self) -> bool:
        """Whether the office switched advertising on in the general list ("Werbung zeigen")."""
        return self.advertising and not (self.category or self.search or self.ai_pending)

    def messages(self, repository: MailRepository) -> list[MessageSummary]:
        """Returns the messages of this list, newest first."""
        return repository.inbox.messages(
            InboxFilter(
                category=self.category,
                status=self.status,
                responsible=self.responsible,
                ai_pending=self.ai_pending,
                search=self.search,
                exclude_category=Category.ADVERTISING if self.hides_advertising else None,
            )
        )

    def hidden_advertising(self, repository: MailRepository) -> int:
        """Returns how many advertising mails this list leaves out ("… Werbung ausgeblendet")."""
        if not self.hides_advertising:
            return 0
        hidden = repository.inbox.messages(
            InboxFilter(category=Category.ADVERTISING, status=self.status, responsible=self.responsible)
        )
        return len(hidden)

    def next_open_message(self, repository: MailRepository, message_id: int) -> int | None:
        """Returns the open message that follows ``message_id`` in this list.

        Used after "erledigt": the office continues with the next mail of the same list. Without a later
        mail the first remaining one is taken; ``None`` means the list is empty.
        """
        ids = [summary.id for summary in replace(self, status=Status.OPEN).messages(repository)]
        if message_id in ids:
            later = ids[ids.index(message_id) + 1 :]
            if later:
                return later[0]
        others = [i for i in ids if i != message_id]
        return others[0] if others else None

    def detail_url(self, message_id: int, notice: str, count: int | None = None) -> str:
        """Returns the address of a message page that shows ``notice`` and keeps this list for "Zurück"."""
        params = {"hinweis": notice, "von": self.query()}
        if count is not None:
            params["anzahl"] = str(count)
        return f"/nachrichten/{message_id}?{urlencode(params)}"


def message_url(message_id: int, notice: str) -> str:
    """Returns the address of a message page with a confirmation and without a list to return to."""
    return f"/nachrichten/{message_id}?hinweis={notice}"


def job_view(status: JobStatus | None) -> dict[str, object] | None:
    """Returns the progress of a job as the page and the progress poll show it.

    Args:
        status: The running or last job of this process, or ``None`` if none ran yet.

    Returns:
        The job values plus a readable label and error text, or ``None`` without a job.
    """
    if status is None:
        return None
    view = status.as_dict()
    view["error_text"] = error_text(status.error)
    view["label"] = KIND_LABELS.get(status.kind, status.kind)
    return view
