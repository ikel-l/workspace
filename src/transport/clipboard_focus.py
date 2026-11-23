import hmac
import logging
import secrets
import socket
import threading
import time
import typing

from . import clipboard_proto as proto

if typing.TYPE_CHECKING:
    from .clipboard_agent import Backend, ClipboardAgent

logger = logging.getLogger(__name__)

ENV_NAME: typing.Final[str] = 'GORIZONTVS_FOCUS_CHANNEL'

GREETING: typing.Final[bytes] = b'GVSFOCUS1 '
MAX_LINE: typing.Final[int] = 128

HELLO_TIMEOUT: typing.Final[float] = 5.0
MAX_PENDING: typing.Final[int] = 4

NO_REPORT_WARNING: typing.Final[float] = 30.0

FOCUS_GRACE: typing.Final[float] = 2.0

Listener = typing.Callable[[typing.Optional[bool]], None]
Log = typing.Callable[[str], None]

_NOTHING: typing.Final[typing.Any] = object()


def _say(log: typing.Optional[Log], line: str) -> None:
    try:
        if log is not None:
            log(line)
        else:
            logger.info('%s', line)
    except Exception:  # pylint: disable=broad-except
        pass


class FocusChannel:

    def __init__(self, log: typing.Optional[Log] = None) -> None:
        self._log = log
        self._token = secrets.token_hex(16)
        self._lock = threading.Lock()
        self._order = threading.RLock()
        self._state: typing.Optional[bool] = None
        self._listener: typing.Optional[Listener] = None
        self._reporter: typing.Optional[socket.socket] = None
        self._reported = False
        self._timer: typing.Optional[threading.Timer] = None
        self._slots = threading.BoundedSemaphore(MAX_PENDING)
        self._closed = threading.Event()
        self._started = False
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._sock.bind(('127.0.0.1', 0))
            self._sock.listen(MAX_PENDING)
        except OSError:
            self._sock.close()
            raise
        self.port: int = self._sock.getsockname()[1]

    @property
    def env(self) -> dict[str, str]:
        return {ENV_NAME: '{}:{}'.format(self.port, self._token)}

    @property
    def state(self) -> typing.Optional[bool]:
        return self._state

    @property
    def stopped(self) -> bool:
        return self._closed.is_set()

    def start(self) -> None:
        with self._lock:
            if self._started or self._closed.is_set():
                return
            self._started = True
        threading.Thread(target=self._accept, name='gvs-focus-accept', daemon=True).start()

    def watch(self, listener: Listener) -> None:
        with self._order:
            self._listener = listener
            self._tell(listener, self._state)
        with self._lock:
            if self._closed.is_set() or self._reported or self._timer is not None:
                return
            self._timer = threading.Timer(NO_REPORT_WARNING, self._silent)
            self._timer.daemon = True
            self._timer.start()

    def stop(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            timer, self._timer = self._timer, None
            reporter, self._reporter = self._reporter, None
        if timer is not None:
            timer.cancel()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        _close(self._sock)
        if reporter is not None:
            _close(reporter, shutdown=True)


    def _silent(self) -> None:
        with self._lock:
            if self._closed.is_set() or self._reported:
                return
        _say(
            self._log,
            'the stream window does not report its focus (an older gorizontvs-protocol?): '
            'nothing is told to the remote desktop',
        )

    @staticmethod
    def _tell(listener: typing.Optional[Listener], state: typing.Optional[bool]) -> None:
        if listener is None:
            return
        try:
            listener(state)
        except Exception:  # pylint: disable=broad-except
            logger.exception('focus listener failed')

    def _set(self, sock: socket.socket, state: typing.Optional[bool]) -> None:
        with self._order:
            with self._lock:
                if self._closed.is_set() or self._reporter is not sock:
                    return
                if state is None:
                    self._reporter = None
                if state == self._state:
                    return
                self._state = state
                listener = self._listener
            self._tell(listener, state)

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                sock, _address = self._sock.accept()
            except OSError:
                if self._closed.is_set():
                    return
                time.sleep(0.2)
                continue
            if not self._slots.acquire(blocking=False):
                _close(sock)
                continue
            threading.Thread(target=self._serve, args=(sock,), name='gvs-focus-read', daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        known = False
        try:
            try:
                sock.settimeout(HELLO_TIMEOUT)
                reader = _Lines(sock, time.monotonic() + HELLO_TIMEOUT)
                hello = reader.next()
                known = (
                    hello is not None
                    and hello.startswith(GREETING)
                    and hmac.compare_digest(hello[len(GREETING):], self._token.encode('ascii'))
                )
            finally:
                self._slots.release()
            if not known:
                return
            with self._order:
                with self._lock:
                    if self._closed.is_set():
                        return
                    previous, self._reporter = self._reporter, sock
                    first, self._reported = not self._reported, True
                    timer, self._timer = self._timer, None
                    # What the earlier connection said is not what this one says
                    changed, self._state = self._state is not None, None
                    listener = self._listener
                if changed:
                    self._tell(listener, None)
            if timer is not None:
                timer.cancel()
            if previous is not None:
                _close(previous, shutdown=True)
            if first:
                _say(self._log, 'the stream window reports its focus')
            sock.settimeout(None)
            reader.deadline = None
            while True:
                line = reader.next()
                if line == b'1':
                    self._set(sock, True)
                elif line == b'0':
                    self._set(sock, False)
                else:
                    return
        except OSError:
            pass
        finally:
            _close(sock)
            if known:
                with self._lock:
                    lost = self._reporter is sock and not self._closed.is_set()
                self._set(sock, None)
                if lost:
                    _say(
                        self._log,
                        'the stream window stopped reporting its focus: nothing is told to the remote desktop',
                    )


class _Lines:

    def __init__(self, sock: socket.socket, deadline: typing.Optional[float] = None) -> None:
        self._sock = sock
        self._buffer = b''
        self.deadline = deadline

    def next(self) -> typing.Optional[bytes]:
        while b'\n' not in self._buffer:
            if len(self._buffer) > MAX_LINE:
                return None
            if self.deadline is not None:
                left = self.deadline - time.monotonic()
                if left <= 0:
                    return None
                self._sock.settimeout(left)
            data = self._sock.recv(256)
            if not data:
                return None
            self._buffer += data
        line, _, self._buffer = self._buffer.partition(b'\n')
        if len(line) > MAX_LINE:
            return None
        return line.rstrip(b'\r')


def _close(sock: socket.socket, shutdown: bool = False) -> None:
    if shutdown:
        try:
            # Wakes up the thread that reads from it
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        sock.close()
    except OSError:
        pass


class FocusedClipboard:

    def __init__(
        self,
        backend: 'Backend',
        channel: typing.Optional[FocusChannel] = None,
        log: typing.Optional[Log] = None,
        grace: float = FOCUS_GRACE,
        clock: typing.Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._channel = channel
        self._log = log
        self._grace = grace
        self._clock = clock
        self._lock = threading.RLock()
        self._agent: typing.Optional['ClipboardAgent'] = None
        self._focus: typing.Optional[bool] = None
        self._lost_at = 0.0
        self._known: typing.Optional[str] = None
        self._real: typing.Any = _NOTHING
        self._placed: typing.Optional[str] = None
        self._copied_away = False
        self._held: typing.Any = _NOTHING
        self._stopped = False

    @property
    def focus(self) -> typing.Optional[bool]:
        return self._focus


    def set_focus(self, state: typing.Optional[bool]) -> None:
        with self._lock:
            if self._stopped:
                return
            previous, self._focus = self._focus, state
            if state is True and previous is not True:
                self._entered()
            elif previous is True and state is not True:
                self._lost_at = self._clock()

    def _entered(self) -> None:
        held, self._held = self._held, _NOTHING
        if held is not _NOTHING:
            if self._copied_away:
                _say(self._log, 'a text of the remote desktop was not applied: something was copied here after it')
            else:
                self._put(held)
        self._copied_away = False
        self._backend.request_current()

    def _put(self, text: str) -> None:
        self._real = self._placed = proto.text_digest(text)
        self._backend.set_text(text)


    def start(self, agent: 'ClipboardAgent') -> None:
        with self._lock:
            self._agent = agent
        self._backend.start(typing.cast('ClipboardAgent', self))
        if self._channel is not None:
            self._channel.watch(self.set_focus)

    def request_current(self) -> None:
        with self._lock:
            if self._stopped or self._agent is None:
                return
            if self._focus is True:
                self._backend.request_current()
                return
            self._agent.local_changed(self._known, answer=True)

    def set_text(self, text: str) -> None:
        with self._lock:
            if self._stopped:
                return
            self._known = text
            if self._focus is False and (self._copied_away or self._clock() - self._lost_at > self._grace):
                self._held = text
                _say(self._log, 'a text of the remote desktop waits for the stream window to be focused')
                return
            self._held = _NOTHING
            self._put(text)

    def clear_if(self, digest: str) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._held is not _NOTHING and proto.text_digest(self._held) == digest:
                self._held = _NOTHING
            if proto.text_digest(self._known) == digest:
                self._known = None
            for present in {digest, self._placed or digest}:
                if self._real == present:
                    self._real = None
                self._backend.clear_if(present)
            self._placed = None

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._held = _NOTHING
            self._known = None
        self._backend.stop()
        if self._channel is not None:
            self._channel.stop()


    def local_changed(self, text: typing.Optional[str], answer: bool = False) -> None:
        with self._lock:
            if self._stopped or self._agent is None:
                return
            digest = proto.text_digest(text)
            if digest != self._placed:
                self._placed = None
            if self._focus is True:
                self._real = digest
                self._known = text
                self._agent.local_changed(text, answer)
                return
            if digest != self._real:
                if not (answer and self._real is _NOTHING):
                    self._copied_away = True
                self._real = digest
            if answer:
                self._agent.local_changed(self._known, answer=True)


def gate(backend: 'Backend', channel: FocusChannel, log: typing.Optional[Log] = None) -> FocusedClipboard:
    return FocusedClipboard(backend, channel, log)
