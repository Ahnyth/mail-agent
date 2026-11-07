"""Small response helpers shared by the routes: redirects after a form post and file downloads."""

from http import HTTPStatus

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

CALENDAR_TYPE = "text/calendar"
CSV_TYPE = "text/csv"


def see_other(url: str) -> RedirectResponse:
    """Redirects to ``url`` after a form post.

    The status "303 See Other" makes the browser load the target with GET, so reloading the page never
    repeats the post.

    Args:
        url: The address to show next.

    Returns:
        The redirect response.
    """
    return RedirectResponse(url, status_code=HTTPStatus.SEE_OTHER)


def text_download(content: str, media_type: str, filename: str) -> Response:
    """Returns a UTF-8 text file that the browser saves instead of showing it.

    Args:
        content: The file content.
        media_type: The type without charset, e.g. :data:`CALENDAR_TYPE`.
        filename: The suggested file name.

    Returns:
        The download response.
    """
    return Response(
        content.encode("utf-8"),
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def base_url(request: Request) -> str:
    """Returns the address of the agent without a trailing slash, used for links inside exported files."""
    return str(request.base_url).rstrip("/")
