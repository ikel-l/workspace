import sys
import typing

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QClipboard, QGuiApplication

from . import clipboard_proto as proto

if typing.TYPE_CHECKING:
    from .clipboard_agent import ClipboardAgent

POLL_INTERVAL_MS: typing.Final[int] = 700


class QtClipboardBackend(QObject):
    _start = Signal()
    _stop = Signal()
    _request = Signal()
    _set = Signal(str)
    _clear = Signal(str)

    def __init__(self, poll: typing.Optional[bool] = None) -> None:
        super().__init__()
        application = QCoreApplication.instance()
        if not isinstance(application, QGuiApplication):
            raise RuntimeError('the clipboard needs a running GUI application')
        self._agent: typing.Optional['ClipboardAgent'] = None
        self._clipboard: typing.Optional[QClipboard] = None
        self._timer: typing.Optional[QTimer] = None
        self._poll = sys.platform == 'darwin' if poll is None else poll
        self._seen: typing.Optional[str] = None
        self._start.connect(self._on_start)
        self._stop.connect(self._on_stop)
        self._request.connect(self._on_request)
        self._set.connect(self._on_set)
        self._clear.connect(self._on_clear)
        self.moveToThread(application.thread())


    def _emit(self, signal: typing.Any, *args: typing.Any) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def start(self, agent: 'ClipboardAgent') -> None:
        self._agent = agent
        self._emit(self._start)

    def request_current(self) -> None:
        self._emit(self._request)

    def set_text(self, text: str) -> None:
        self._emit(self._set, text)

    def clear_if(self, digest: str) -> None:
        self._emit(self._clear, digest)

    def stop(self) -> None:
        self._emit(self._stop)


    def _text(self) -> typing.Optional[str]:
        if self._clipboard is None:
            return None
        mime = self._clipboard.mimeData(QClipboard.Mode.Clipboard)
        if mime is None or not mime.hasText():
            return None
        return mime.text() or None

    def _report(self, answer: bool = False) -> None:
        if self._agent is None or self._clipboard is None:
            return
        text = self._text()
        self._seen = proto.text_digest(text) if text else None
        self._agent.local_changed(text, answer)

    @Slot()
    def _on_changed(self) -> None:
        self._report()

    @Slot()
    def _on_start(self) -> None:
        if self._clipboard is not None:
            return
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_changed)
        if self._poll:
            self._seen = proto.text_digest(self._text())
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_INTERVAL_MS)
            self._timer.timeout.connect(self._on_poll)
            self._timer.start()

    @Slot()
    def _on_poll(self) -> None:
        if proto.text_digest(self._text()) != self._seen:
            self._report()

    @Slot()
    def _on_request(self) -> None:
        self._report(answer=True)

    @Slot(str)
    def _on_set(self, text: str) -> None:
        if self._clipboard is None:
            return
        self._seen = proto.text_digest(text)
        self._clipboard.setText(text, QClipboard.Mode.Clipboard)

    @Slot(str)
    def _on_clear(self, digest: str) -> None:
        if self._clipboard is None:
            return
        if proto.text_digest(self._text()) == digest:
            self._seen = None
            self._clipboard.clear(QClipboard.Mode.Clipboard)

    @Slot()
    def _on_stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._clipboard is not None:
            try:
                self._clipboard.dataChanged.disconnect(self._on_changed)
            except (RuntimeError, TypeError):
                pass
            self._clipboard = None
        self._agent = None
        self.deleteLater()
