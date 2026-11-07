"""Icon next to the clock while the agent runs invisibly in the background.

The background start (``background.py``) has no window, so without this icon nobody could see whether the
agent runs, or stop it. A left click opens the web interface; the right-click menu offers "Öffnen" and
"Beenden". "Beenden" asks first only while a fetch or AI analysis is running. pystray is imported only here,
so the console start and the tests never need a taskbar.
"""

import sys
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .jobs import JobStatus, running_text
from .security.redaction import get_logger

_log = get_logger("tray")

TITLE = "MailAgent läuft – Klick öffnet, Rechtsklick: Beenden"
"""Tooltip; Windows cuts it after 127 characters."""
BRAND = (107, 63, 160)
"""``--brand`` from ``style.css``, so the icon matches the logo in the web interface."""
ICON_SIZE = 64
"""Pixels per side; Windows scales the image down to the 16 to 32 pixels of the taskbar."""
CORNER_RADIUS_DIVISOR = 5
"""The corner radius is a fifth of the side: soft corners like the logo, still clearly a square."""
LETTER_SHARE = 0.72
"""Font size of the "M" relative to the side: large, so it stays readable when Windows shrinks the icon."""
FONT_FILE = "segoeuib.ttf"
"""Segoe UI Bold, part of every Windows; elsewhere Pillow's built-in font stands in."""
WAIT_ROUNDS = 120
"""How often the thread checks whether the web interface answers before giving up."""
WAIT_STEP_SECONDS = 0.5
"""The icon appears once the web interface answers; at most 60 seconds (every wait has an upper bound)."""
STOP_TIMEOUT_SECONDS = 3.0
"""How long :meth:`TrayIcon.stop` waits for the icon thread to end (every wait has an upper bound)."""

# Flags and return value of the Win32 function MessageBoxW (winuser.h).
_MB_YESNO = 0x04
"""Buttons "Ja" and "Nein"."""
_MB_ICONQUESTION = 0x20
"""The question mark icon."""
_MB_DEFBUTTON2 = 0x100
"""The second button ("Nein") is preselected, so a hasty Enter keeps the agent running."""
_MB_SETFOREGROUND = 0x10000
"""The box comes to the front."""
_MB_TOPMOST = 0x40000
"""The box stays above other windows."""
_IDYES = 6
"""MessageBoxW returns this when "Ja" was clicked."""


def icon_image(size: int = ICON_SIZE) -> Image.Image:
    """Draws the "M" logo of the web interface: a white letter on a rounded violet square."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // CORNER_RADIUS_DIVISOR, fill=BRAND)
    font_size = int(size * LETTER_SHARE)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype(FONT_FILE, font_size)
    except OSError:
        font = ImageFont.load_default(font_size)
    draw.text((size / 2, size / 2), "M", fill="white", font=font, anchor="mm")
    return image


def quit_question(status: JobStatus | None) -> str | None:
    """Returns the question before stopping while a job runs; ``None`` means stop without asking."""
    running = running_text(status)
    if running is None:
        return None
    return (
        f"Gerade läuft: {running}.\n\nTrotzdem beenden? Bereits Erledigtes bleibt gespeichert, "
        "der Rest wird beim nächsten Abruf nachgeholt."
    )


def ask_yes_no(question: str) -> bool:
    """Shows a Windows message box with "Ja" and "Nein" ("Nein" preselected); elsewhere always yes."""
    if sys.platform != "win32":
        return True
    import ctypes  # noqa: PLC0415 - Windows only; ctypes.windll does not exist elsewhere

    flags = _MB_YESNO | _MB_ICONQUESTION | _MB_DEFBUTTON2 | _MB_SETFOREGROUND | _MB_TOPMOST
    answer: int = ctypes.windll.user32.MessageBoxW(None, question, "MailAgent beenden", flags)
    return answer == _IDYES


class TrayIcon:
    """The icon in its own thread; ``request_quit`` stops the web server, which then calls :meth:`stop`."""

    def __init__(
        self,
        url: str,
        request_quit: Callable[[], None],
        job_status: Callable[[], JobStatus | None],
        *,
        ask: Callable[[str], bool] = ask_yes_no,
        open_url: Callable[[str], object] = webbrowser.open,
    ) -> None:
        """Prepares the icon; it appears only after :meth:`start`.

        Args:
            url: The address of the web interface.
            request_quit: Stops the web server.
            job_status: Returns the running fetch or analysis, if any.
            ask: Asks a yes/no question; tests pass a fake instead of a real message box.
            open_url: Opens the web interface; tests pass a fake instead of the browser.
        """
        self._url = url
        self._request_quit = request_quit
        self._job_status = job_status
        self._ask = ask
        self._open_url = open_url
        self._closed = threading.Event()
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    def open(self) -> None:
        """Opens the web interface in the browser (left click and "Öffnen")."""
        self._open_url(self._url)

    def quit(self) -> None:
        """Stops the agent ("Beenden"), asking first while a job runs."""
        question = quit_question(self._job_status())
        if question is not None and not self._ask(question):
            return
        _log.info("Beenden über das Symbol in der Taskleiste")
        self._request_quit()

    def start(self, ready: Callable[[], bool]) -> None:
        """Shows the icon as soon as ``ready()`` says the web interface answers."""
        # A daemon thread never keeps the process alive; pystray's own helper thread inherits that.
        self._thread = threading.Thread(target=self._run, args=(ready,), name="taskleiste", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Removes the icon (otherwise Windows shows a dead icon until the mouse passes over it)."""
        self._closed.set()
        icon = self._icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                _log.exception("Symbol in der Taskleiste: Entfernen fehlgeschlagen")
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_SECONDS)

    def _run(self, ready: Callable[[], bool]) -> None:
        """Waits for the web interface, then shows the icon until :meth:`stop`; runs in the icon thread."""
        if not self._wait_until_ready(ready):
            return
        try:
            self._show()
        except Exception:
            _log.exception("Symbol in der Taskleiste nicht verfügbar – Beenden geht über die Weboberfläche")

    def _wait_until_ready(self, ready: Callable[[], bool]) -> bool:
        """Waits at most :data:`WAIT_ROUNDS` steps for ``ready()``; ``False`` if :meth:`stop` came first."""
        for _ in range(WAIT_ROUNDS):
            if self._closed.is_set() or ready():
                break
            self._closed.wait(WAIT_STEP_SECONDS)
        return not self._closed.is_set()

    def _show(self) -> None:
        """Creates the icon with its menu and runs it; returns when the icon is stopped."""
        import pystray  # noqa: PLC0415 - needs a desktop session; console start and tests never load it

        menu = pystray.Menu(
            pystray.MenuItem("MailAgent öffnen", self.open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self.quit),
        )
        self._icon = pystray.Icon("mail-agent", icon_image(), TITLE, menu)
        self._icon.run(setup=self._setup)

    def _setup(self, icon: Any) -> None:
        """Makes the icon visible once pystray is ready; stops it at once if :meth:`stop` came earlier."""
        icon.visible = True
        _log.info("Symbol in der Taskleiste angezeigt")
        if self._closed.is_set():  # stop() came before the icon was ready
            icon.stop()
