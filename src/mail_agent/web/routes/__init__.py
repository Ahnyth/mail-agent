"""The routes of the web interface, one module per area of the page."""

from fastapi import APIRouter

from . import (
    briefing,
    corrections,
    documents,
    fetch,
    handover,
    inbox,
    lists,
    messages,
    senders,
    shutdown,
    voice,
)

ROUTERS: tuple[APIRouter, ...] = (
    inbox.router,
    fetch.router,
    messages.router,
    senders.router,
    corrections.router,
    handover.router,
    briefing.router,
    lists.router,
    voice.router,
    documents.router,
    shutdown.router,
)
"""All routers in the order they are added to the application."""
