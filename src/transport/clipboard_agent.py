import logging
import socket
import threading
import time
import typing

from . import clipboard_proto as proto

logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL: typing.Final[float] = 20.0
SILENCE_TIMEOUT: typing.Final[float] = 60.0

MAX_PENDING_HANDSHAKES: typing.Final[int] = 4

INITIAL_REPORT_TIMEOUT: typing.Final[float] = 2.0

RECONNECT_DELAYS: typing.Final[tuple[float, ...]] = (0.5, 1.0, 2.0, 5.0, 10.0, 15.0)
MAX_FAILED_CONNECTS: typing.Final[int] = 12

LOCAL: typing.Final[str] = 'local'
REMOTE: typing.Final[str] = 'remote'


class Backend(typing.Protocol):
    """The clipboard of this end. Every method returns at once and may be called from any thread."""

    def start(self, agent: 'ClipboardAgent') -> None:
        """Start calling agent.local_changed(text) on every change of the clipboard, in their order."""

    def request_current(self) -> None:
        """Call agent.local_changed(text, answer=True) with what the clipboard holds now."""

    def set_text(self, text: str) -> None:
        """Put the text into the clipboard."""

    def clear_if(self, digest: str) -> None:
        """Empty the clipboard if it still holds the text with this digest."""

    def stop(self) -> None:
        """Stop watching the clipboard."""


class _Link:

    def __init__(
        self, agent: 'ClipboardAgent', channel: proto.Channel, sock: socket.socket, gated: bool = False
    ) -> None:
        self._agent = agent
        self._channel = channel
        self._sock = sock
        self._wake = threading.Condition()
        self._pending: typing.Optional[tuple[int, bytes]] = None
        self._announce = False
        self._lost = False
        self._closed = False
        self._gate = threading.Event()
        if not gated:
            self._gate.set()
        self.ready = False
        self.done = threading.Event()

    def start(self) -> None:
        self._sock.settimeout(SILENCE_TIMEOUT)
        threading.Thread(target=self._read, name='gvs-clipboard-read', daemon=True).start()
        threading.Thread(target=self._write, name='gvs-clipboard-write', daemon=True).start()

    def offer(self, kind: int, payload: bytes = b'') -> None:
        with self._wake:
            if self._closed:
                return
            self._pending = (kind, payload)
            self._wake.notify()

    def announce(self) -> None:
        with self._wake:
            if self._closed:
                return
            self._announce = True
            self._wake.notify()

    def open(self) -> None:
        self._gate.set()

    def close(self) -> None:
        with self._wake:
            if self._closed:
                return
            self._closed = True
            lost = self._lost or self._pending is not None
            self._wake.notify()
        self._gate.set()
        self._channel.close()
        try:
            self._agent._detached(self, lost)  # pylint: disable=protected-access
        finally:
            self.done.set()

    def _read(self) -> None:
        try:
            if not self._gate.wait(INITIAL_REPORT_TIMEOUT):
                self._agent._report_missed(self)  # pylint: disable=protected-access
            while True:
                kind, payload = self._channel.recv()
                self._agent._received(self, kind, payload)  # pylint: disable=protected-access
        except proto.ProtocolError as e:
            if not self._closed:
                self._agent._log('channel closed: %s', e)  # pylint: disable=protected-access
        except Exception as e:  # pylint: disable=broad-except
            self._agent._log('channel failed: %s: %s', e.__class__.__name__, e)  # pylint: disable=protected-access
        finally:
            self.close()

    def _write(self) -> None:
        try:
            while True:
                with self._wake:
                    if not self._closed and self._pending is None and not self._announce:
                        self._wake.wait(KEEPALIVE_INTERVAL)
                    if self._closed:
                        return
                    record, self._pending = self._pending, None
                    announce = record is None and self._announce
                    if announce:
                        self._announce = False
                    self._lost = record is not None
                if record is not None:
                    self._channel.send(*record)
                    with self._wake:
                        self._lost = False
                elif announce:
                    self._channel.send(proto.READY)
                else:
                    self._channel.send(proto.PING)
        except Exception:  # pylint: disable=broad-except
            pass 
        finally:
            self.close()


class ClipboardAgent:
    def __init__(
        self,
        role: str,
        secret: bytes,
        policy: proto.Policy,
        backend: Backend,
        launch_id: str = '',
        log: typing.Optional[typing.Callable[[str], None]] = None,
    ) -> None:
        if role not in (proto.ROLE_CLIENT, proto.ROLE_HOST):
            raise ValueError('unknown role')
        if len(secret) != proto.SECRET_SIZE:
            raise ValueError('wrong size of the secret')
        self.role = role
        self.launch_id = launch_id
        self._secret = secret
        self._policy = policy
        self._backend = backend
        self._log_sink = log
        self._lock = threading.RLock()
        self._link: typing.Optional[_Link] = None
        self._agreed = proto.Policy()
        self._digest: typing.Optional[str] = None
        self._origin = LOCAL
        self._undelivered = role == proto.ROLE_CLIENT
        self._telling: typing.Optional[_Link] = None
        self._started = False
        self._stopped = False

    def start(self) -> None:
        with self._lock:
            if self._started or self._stopped:
                return
            self._started = True
        self._backend.start(self)

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            link, self._link = self._link, None
            leftover = self._digest if self._origin == REMOTE else None
        if link is not None:
            link.close()
        if self._started:
            if leftover:
                self._backend.clear_if(leftover)
            self._backend.stop()
        self._log('stopped')

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def connected(self) -> bool:
        return self._link is not None


    def serve(self, sock: socket.socket) -> typing.Optional[_Link]:
        try:
            channel, agreed = proto.handshake(
                sock, self.role, self._secret, self._policy, timeout=proto.HANDSHAKE_TIMEOUT
            )
        except proto.ProtocolError as e:
            self._log('connection refused: %s', e)
            _close(sock)
            return None
        except Exception as e:  # pylint: disable=broad-except
            self._log('connection failed: %s: %s', e.__class__.__name__, e)
            _close(sock)
            return None

        client = self.role == proto.ROLE_CLIENT
        link = _Link(self, channel, sock, gated=client)
        with self._lock:
            if self._stopped:
                channel.close()
                return None
            previous, self._link = self._link, link
            self._agreed = agreed
            tell = client and self._undelivered and agreed.allows_sending(self.role)
            self._telling = link if tell else None
        if previous is not None:
            previous.close()
        link.start()
        self._log('connected, mode %s, up to %s bytes', agreed.mode, agreed.max_size)
        if tell:
            self._backend.request_current()
        elif client:
            self._told(link)
        return link

    @staticmethod
    def _told(link: _Link) -> None:
        link.announce()
        link.open()

    def _report_missed(self, link: _Link) -> None:
        with self._lock:
            if self._telling is not link:
                return
            self._telling = None
        self._log('the clipboard did not answer in time')
        link.announce()

    def _detached(self, link: _Link, lost: bool = False) -> None:
        with self._lock:
            if self._telling is link:
                self._telling = None
            if self._link is link:
                self._link = None
            if lost and not self._stopped:
                self._undelivered = True

            retry = lost and not self._stopped and self._started and self._link is not None
        if retry:
            self._backend.request_current()


    def local_changed(self, text: typing.Optional[str], answer: bool = False) -> None:
        try:
            digest = proto.text_digest(text)
            payload = proto.encode_text(text) if text else b''
        except UnicodeError:
            digest, payload = 'invalid', b''
        told: typing.Optional[_Link] = None
        try:
            with self._lock:
                if self._stopped:
                    return
                told, self._telling = self._telling, None
                if self._origin == REMOTE and digest == self._digest:
                    return 
                previous = self._digest
                owed = self._undelivered
                if answer and not owed and digest == previous:
                    return
                self._digest, self._origin = digest, LOCAL
                link = self._link
                allowed = self._agreed.allows_sending(self.role)
                limit = self._agreed.max_size
                waiting = link is not None and self.role == proto.ROLE_HOST and not link.ready
                self._undelivered = link is None or waiting
            if link is None or waiting or not allowed:
                return
            size = max(len(payload) - 1, 0)
            carried = bool(payload) and '\x00' not in typing.cast(str, text)
            if carried and size <= limit:
                link.offer(proto.DATA, payload)
                self._audit('out', size, 'sent')
                return
            if payload:
                self._audit('out', size, 'too_large' if carried else 'not_text')
            if previous is not None or payload or owed:
                link.offer(proto.CLEAR)
        finally:
            if told is not None:
                self._told(told)


    def _received(self, link: _Link, kind: int, payload: bytes) -> None:
        if kind == proto.PING:
            return
        if kind == proto.READY:
            with self._lock:
                heard = self.role == proto.ROLE_HOST and self._link is link and not self._stopped
                if heard:
                    link.ready = True
                tell = heard and self._undelivered and self._agreed.allows_sending(self.role)
            if tell:
                self._backend.request_current()
            return
        if kind == proto.ERROR:
            self._log('the peer reported: %s', payload[:200].decode('utf-8', errors='replace'))
            return
        if kind not in (proto.DATA, proto.CLEAR):
            raise proto.ProtocolError('unknown record type {}'.format(kind))

        with self._lock:
            current = self._link is link and not self._stopped
            allowed = self._agreed.allows_receiving(self.role)
            limit = self._agreed.max_size
        if not current:
            return
        if not allowed:
            self._audit('in', max(len(payload) - 1, 0), 'denied')
            raise proto.ProtocolError('the peer sent a clipboard against the policy')

        if kind == proto.CLEAR:
            with self._lock:
                held = self._digest if self._origin == REMOTE else None
                if held:
                    self._digest = None
            if held:
                self._backend.clear_if(held)
                self._audit('in', 0, 'cleared')
            return

        size = max(len(payload) - 1, 0)
        if size > limit:
            self._audit('in', size, 'too_large')
            raise proto.ProtocolError('the peer sent more than the policy allows')
        try:
            text = proto.decode_text(payload)
        except proto.ProtocolError:
            self._audit('in', size, 'not_text')
            raise
        digest = proto.text_digest(text)
        with self._lock:
            if self._stopped:
                return
            same = digest == self._digest
            if not same:
                self._digest, self._origin = digest, REMOTE
                self._undelivered = False
        if same:
            self._audit('in', size, 'unchanged')
            return
        self._backend.set_text(text)
        self._audit('in', size, 'applied')

    def _log(self, message: str, *args: typing.Any) -> None:
        line = 'clipboard {}{}: {}'.format(
            self.role, ' launch {}'.format(self.launch_id) if self.launch_id else '', message % args
        )
        try:
            if self._log_sink is not None:
                self._log_sink(line)
            else:
                logger.info('%s', line)
        except Exception:  # pylint: disable=broad-except
            pass

    def _audit(self, direction: str, size: int, result: str) -> None:
        self._log('transfer direction=%s size=%s result=%s', direction, size, result)


def _close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


class HostEndpoint:

    def __init__(self, agent: ClipboardAgent, port: int, address: str = '', lifetime: float = 0) -> None:
        self._agent = agent
        self._lifetime = lifetime
        self._slots = threading.BoundedSemaphore(MAX_PENDING_HANDSHAKES)
        self._closed = threading.Event()
        self._timer: typing.Optional[threading.Timer] = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
            else:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((address, port))
            self._sock.listen(8)
        except OSError:
            self._sock.close()
            raise
        self.port: int = self._sock.getsockname()[1]

    def start(self) -> None:
        self._agent.start()
        threading.Thread(target=self._accept, name='gvs-clipboard-accept', daemon=True).start()
        if self._lifetime > 0:
            self._timer = threading.Timer(self._lifetime, self.stop)
            self._timer.daemon = True
            self._timer.start()

    def stop(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._timer is not None:
            self._timer.cancel()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        _close(self._sock)
        self._agent.stop()

    @property
    def stopped(self) -> bool:
        return self._closed.is_set()

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
            threading.Thread(target=self._handshake, args=(sock,), name='gvs-clipboard-hello', daemon=True).start()

    def _handshake(self, sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._agent.serve(sock)
        except Exception:  # pylint: disable=broad-except
            _close(sock)
        finally:
            self._slots.release()


class ClientEndpoint:

    def __init__(self, agent: ClipboardAgent, address: tuple[str, int]) -> None:
        self._agent = agent
        self._address = address
        self._closed = threading.Event()

    def start(self) -> None:
        self._agent.start()
        threading.Thread(target=self._run, name='gvs-clipboard-connect', daemon=True).start()

    def stop(self) -> None:
        self._closed.set()
        self._agent.stop()

    @property
    def stopped(self) -> bool:
        return self._closed.is_set()

    def _run(self) -> None:
        failures = 0
        while not self._closed.is_set():
            link = None
            try:
                sock = socket.create_connection(self._address, timeout=proto.HANDSHAKE_TIMEOUT)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                link = self._agent.serve(sock)
            except OSError:
                link = None
            if link is not None:
                failures = 0
                while not link.done.wait(1.0):
                    if self._closed.is_set():
                        link.close()
                        return
                continue_after = RECONNECT_DELAYS[0]
            else:
                failures += 1
                if failures >= MAX_FAILED_CONNECTS:
                    self._agent._log(  # pylint: disable=protected-access
                        'giving up after %s failed connections', failures
                    )
                    self.stop()
                    return
                continue_after = RECONNECT_DELAYS[min(failures - 1, len(RECONNECT_DELAYS) - 1)]
            self._closed.wait(continue_after)
