"""Filed attachments: opening or downloading a document from the filing folder."""

from http import HTTPStatus
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..context import Context, WebContext
from ..security import PDF_POLICY

router = APIRouter()

INLINE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
"""Types the browser may show directly; everything else is offered as a download."""
DOCUMENT_NOT_FOUND = "Dokument nicht gefunden"


@router.get("/dokumente/{document_id}")
def download(document_id: int, context: Context) -> FileResponse:
    """Returns a filed document; only files inside the filing folder can be reached."""
    document = context.repository.documents.get(document_id)
    if document is None or document.stored_path is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, DOCUMENT_NOT_FOUND)
    return file_response(filed_path(context, document.stored_path), document.filename)


def filed_path(context: WebContext, stored_path: str) -> Path:
    """Returns the file of a document, or ends the request with 404 if it left the filing folder."""
    try:
        path = context.service.documents.resolve(stored_path)
    except ValueError:  # the stored path points outside the filing folder
        raise HTTPException(HTTPStatus.NOT_FOUND, DOCUMENT_NOT_FOUND) from None
    if not path.is_file():
        raise HTTPException(HTTPStatus.NOT_FOUND, "Dokument nicht mehr in der Ablage")
    return path


def file_response(path: Path, filename: str) -> FileResponse:
    """Returns the file, shown in the browser for known types and as a download otherwise."""
    media_type = INLINE_TYPES.get(path.suffix.lower())
    response = FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=filename,
        content_disposition_type="inline" if media_type else "attachment",
    )
    if media_type == "application/pdf":
        # The built-in PDF viewer of Edge and Chrome is a plugin, which "object-src 'none'" of the page
        # policy would block (blank tab). PDFs get their own policy without any scripts of this origin.
        response.headers["Content-Security-Policy"] = PDF_POLICY
    return response
