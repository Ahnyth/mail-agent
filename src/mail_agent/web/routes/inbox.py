"""The inbox: the filtered message list with the state of the fetch and of the hand-over."""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ...models import Category, Status, Urgency
from ...service import SENT_CHECKED_STATE, SENT_MAILBOX_STATE, MailAgentService
from ...service.execution import KIND_FETCH
from ..context import Context, WebContext
from ..forms import InboxQuery
from ..responses import see_other
from ..texts import TRIGGERS, error_text
from ..views import ListView, job_view

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, context: Context, query: Annotated[InboxQuery, Query()]) -> Response:
    """Shows the inbox with the filters from the address bar."""
    page = InboxPage(context, query.view())
    return context.render(request, "index.html", page.data(query.notice()))


@router.post("/werbung-erledigt")
def advertising_done(context: Context) -> RedirectResponse:
    """Marks all open advertising as done ("Alle Werbung erledigen")."""
    repository = context.repository
    count = repository.workflow.set_status_many(
        repository.workflow.open_ids(Category.ADVERTISING), Status.DONE
    )
    return see_other(f"/?hinweis=werbung_erledigt&anzahl={count}")


class InboxPage:
    """Collects the template data of the inbox; one small method per part of the page."""

    def __init__(self, context: WebContext, view: ListView) -> None:
        """Prepares the page for the list described by ``view``."""
        self._context = context
        self._view = view

    def data(self, notice: str | None) -> dict[str, object]:
        """Returns all values that ``index.html`` needs."""
        return {
            "notice": notice,
            "high": Urgency.HIGH,
            **self._list(),
            **self._advertising(),
            **self._fetch(),
            **self._sent_folder(),
        }

    def _list(self) -> dict[str, object]:
        """The messages of the current list together with the state of the filters."""
        view, repository = self._view, self._context.repository
        return {
            "messages": view.messages(repository),
            "counts": repository.inbox.counts(),
            "active_category": view.category,
            "active_responsible": view.responsible,
            "active_status": view.status.value if view.status else "alle",
            "ai_filter": view.ai_pending,
            "search": view.search,
            "list_query": view.query(),
            "clear_query": replace(view, search="").query(),
            "uses_ai": self._context.service.uses_ai,
        }

    def _advertising(self) -> dict[str, object]:
        """The line "… Werbung ausgeblendet" and the switch to show or hide advertising."""
        view = self._view
        return {
            "hidden_advertising": view.hidden_advertising(self._context.repository),
            "showing_advertising": view.shows_advertising_on_request,
            "show_ads_query": replace(view, advertising=True).query(),
            "hide_ads_query": replace(view, advertising=False).query(),
        }

    def _fetch(self) -> dict[str, object]:
        """The running job, the schedule, the last fetch and whether this PC may fetch at all."""
        service = self._context.service
        last_run = self._context.repository.runs.last(KIND_FETCH)
        return {
            "job": job_view(self._context.jobs.status()),
            "fetch_enabled": service.may_fetch(),
            "handover": handover_banner(service),
            "start_date": service.start_date(),
            "fetch_times": [t.strftime("%H:%M") for t in service.fetch.times],
            "next_run": self._context.schedule.next_run(),
            "last_run": last_run,
            "last_run_trigger": TRIGGERS.get(last_run.trigger, last_run.trigger) if last_run else None,
            "last_run_error": error_text(last_run.error) if last_run else None,
        }

    def _sent_folder(self) -> dict[str, object]:
        """Which sent folder is searched for answers, and when that happened last."""
        service, repository = self._context.service, self._context.repository
        configured = service.settings.imap.sent_mailbox if service.settings else ""
        return {
            "sent_mailbox": configured or repository.state.get(SENT_MAILBOX_STATE),
            "sent_checked": repository.state.get(SENT_CHECKED_STATE),
        }


def handover_banner(service: MailAgentService) -> dict[str, object] | None:
    """Returns what the inbox shows about the hand-over, or ``None`` without a hand-over folder."""
    handover = service.handover
    if handover is None or not handover.configured:
        return None
    remote = handover.remote_header()
    return {
        "active": handover.is_active(),
        "remote_newer": handover.remote_newer(),
        "remote_pc": remote.pc if remote else "",
        "remote_created": remote.created.astimezone() if remote else None,
    }
