"""The overview pages across all messages: deadlines, invoices, applications and follow-ups."""

from datetime import date

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from ...applications import Checklist, checklist
from ...briefing import DeadlineItem, deadline_items, invoice_rows, unanswered
from ...exports import deadlines_ics, invoices_csv
from ...models import Category, Status
from ...storage import MessageDetail
from ..context import Context, WebContext
from ..responses import CALENDAR_TYPE, CSV_TYPE, base_url, text_download

router = APIRouter()

MAX_FOLLOW_UP_DAYS = 365
"""Longer waiting times make no sense for a reminder; larger values fall back to the configured one."""


@router.get("/fristen", response_class=HTMLResponse)
def deadlines_page(request: Request, context: Context) -> Response:
    """Shows all deadlines of the open messages, the most urgent first."""
    return context.render(request, "fristen.html", {"items": open_deadlines(context)})


@router.get("/fristen.ics")
def deadlines_export(request: Request, context: Context) -> Response:
    """Exports the deadlines that have not passed yet as a calendar file."""
    upcoming = [item for item in open_deadlines(context) if item.days_left >= 0]
    return text_download(deadlines_ics(upcoming, base_url(request)), CALENDAR_TYPE, "fristen.ics")


@router.get("/rechnungen", response_class=HTMLResponse)
def invoices_page(request: Request, context: Context) -> Response:
    """Shows all invoices and payment requests with the checks of their bank details."""
    rows = invoice_rows(context.repository.messages.details(), context.service.senders)
    return context.render(request, "rechnungen.html", {"rows": rows})


@router.get("/rechnungen.csv")
def invoices_export(context: Context) -> Response:
    """Exports the invoice list for the accounting, e.g. for Excel."""
    content = invoices_csv(invoice_rows(context.repository.messages.details(), context.service.senders))
    return text_download(content, CSV_TYPE, f"rechnungen-{date.today():%Y-%m-%d}.csv")


@router.get("/bewerbungen", response_class=HTMLResponse)
def applications_page(request: Request, context: Context) -> Response:
    """Shows the applications with the checklist of documents that came with them."""
    return context.render(
        request, "bewerbungen.html", {"rows": application_rows(context.repository.messages.details())}
    )


@router.get("/wiedervorlage", response_class=HTMLResponse)
def follow_up_page(request: Request, context: Context, tage: str = "") -> Response:
    """Shows open messages that need a reply and have waited longer than ``tage`` days."""
    days = follow_up_days(tage, context.service.workflow.follow_up_days)
    messages = unanswered(context.repository.messages.details(status=Status.OPEN), date.today(), days)
    return context.render(request, "wiedervorlage.html", {"messages": messages, "days": days})


def open_deadlines(context: WebContext) -> list[DeadlineItem]:
    """Returns the deadlines of all open messages, sorted by the days left."""
    return deadline_items(context.repository.messages.details(status=Status.OPEN), date.today(), None)


def application_rows(details: list[MessageDetail]) -> list[tuple[MessageDetail, Checklist]]:
    """Returns the applications with their checklists: open ones first, the newest at the top."""
    rows = [
        (m, checklist(m.subject, m.body, [(d.filename, d.extracted_text) for d in m.documents]))
        for m in details
        if m.analysis.category is Category.APPLICATION
    ]
    rows.sort(key=lambda row: (row[0].status is not Status.OPEN, -row[0].id))
    return rows


def follow_up_days(value: str, default: int) -> int:
    """Returns the waiting time from the address, or ``default`` for a missing or implausible value."""
    if value.isdigit() and int(value) <= MAX_FOLLOW_UP_DAYS:
        return int(value)
    return default
