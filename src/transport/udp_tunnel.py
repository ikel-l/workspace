from __future__ import annotations

import contextlib
import logging
import selectors
import socket
import ssl
import threading
import time
import typing

from src.transport import consts as tc
from src.transport import udp_proto
from src.utils.types import ForwardState

log = logging.getLogger(__name__)

PATH_CHECK_TIMEOUT: typing.Final[float] = 5.0
PATH_CHECK_INTERVAL: typing.Final[float] = 0.25
TICK: typing.Final[float] = 0.5
LOST_AFTER_INTERVALS: typing.Final[int] = 3
SOCKET_BUFFER: typing.Final[int] = 4 * 1024 * 1024


def _enlarge_buffers(sock: socket.socket) -> None:
    for option in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, option, SOCKET_BUFFER)


class UdpTunnelError(ConnectionError):
    """UDP tunnel could not be established."""


class UdpTunnelUnsupported(UdpTunnelError):
    """Tunnel Server does not implement the UDP command."""


def _connect_control(
    remote: typing.Tuple[str, int], use_ipv6: bool, check_certificate: bool
) -> ssl.SSLSocket:
    from src.transport.tunnel import ForwardServer

    return ForwardServer._connect(remote, use_ipv6, check_certificate) 


def _recv_exact(sock: ssl.SSLSocket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise UdpTunnelError("Tunnel server closed the connection during UDP setup")
        data += chunk
    return data


class UdpForwarder:

    remote: typing.Tuple[str, int]
    server_address: typing.Tuple[str, int]
    status: ForwardState

    def __init__(
        self,
        remote: typing.Tuple[str, int],
        ticket: str,
        timeout: int = 0,
        local_port: int = 0,
        check_certificate: bool = True,
        use_ipv6: bool = False,
        ipv6_listen: bool = False,
        path_timeout: float = PATH_CHECK_TIMEOUT,
    ) -> None:
        self.remote = remote
        self.remote_ipv6 = use_ipv6 or ":" in remote[0]
        self.timeout = abs(timeout) if timeout else 0
        self.stop_flag = threading.Event()
        self.status = ForwardState.TUNNEL_LISTENING
        self.sent = 0  
        self.received = 0  
        self.dropped = 0  
        self.peer: typing.Optional[typing.Tuple[typing.Any, ...]] = None
        self._lock = threading.RLock()
        self._closed = False
        self._thread: typing.Optional[threading.Thread] = None
        self._control: typing.Optional[ssl.SSLSocket] = None
        self._udp: typing.Optional[socket.socket] = None
        self._mtu_warned = False

        family = socket.AF_INET6 if ipv6_listen else socket.AF_INET
        self._local = socket.socket(family, socket.SOCK_DGRAM)
        _enlarge_buffers(self._local)
        try:
            self._local.bind((tc.LISTEN_ADDRESS_V6 if ipv6_listen else tc.LISTEN_ADDRESS, local_port))
            self.server_address = self._local.getsockname()[:2]
            self._setup(ticket, check_certificate, path_timeout)
        except BaseException:
            self._close_sockets()
            raise

    def _setup(self, ticket: str, check_certificate: bool, path_timeout: float) -> None:
        control = _connect_control(self.remote, self.remote_ipv6, check_certificate)
        self._control = control
        control.settimeout(max(path_timeout, 5.0) * 2)
        control.sendall(tc.CMD_UDP_OPEN + ticket.encode())
        try:
            setup = udp_proto.read_setup_response(
                lambda n: _recv_exact(control, n), lambda: control.recv(128)
            )
        except ConnectionError as e:
            message = str(e)
            if isinstance(e, UdpTunnelError):
                raise
            if message.startswith("ERROR_COMMAND"):
                raise UdpTunnelUnsupported(
                    "The tunnel server does not support UDP tunnels. Please, update it."
                ) from None
            if message.startswith("ERROR_TICKET"):
                raise UdpTunnelError("The tunnel server rejected the UDP ticket") from None
            raise UdpTunnelError(f"UDP tunnel error: {message or 'connection closed'}") from None
        except udp_proto.ProtocolError as e:
            raise UdpTunnelError(f"Invalid UDP tunnel setup: {e}") from None

        self.session = udp_proto.Session(setup, "client")
        self.setup = setup

        family = socket.AF_INET6 if self.remote_ipv6 else socket.AF_INET
        udp = socket.socket(family, socket.SOCK_DGRAM)
        self._udp = udp
        _enlarge_buffers(udp)
        udp.connect((self.remote[0], setup.port))
        self._verify_path(path_timeout)
        control.setblocking(False)

        log.info(
            "UDP tunnel %s -> %s:%s (session %s, keepalive %ss, mtu %s)",
            self.server_address,
            self.remote[0],
            setup.port,
            setup.session_id.hex()[:8],
            setup.keepalive,
            setup.mtu,
        )

    def _verify_path(self, path_timeout: float) -> None:
        udp = typing.cast(socket.socket, self._udp)
        deadline = time.monotonic() + path_timeout
        udp.settimeout(PATH_CHECK_INTERVAL)
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError):
                udp.send(self.session.seal(udp_proto.TYPE_KEEPALIVE))
            try:
                datagram = udp.recv(udp_proto.MAX_DATAGRAM)
            except (socket.timeout, ConnectionRefusedError, OSError):
                continue
            try:
                packet_type, _payload, _newest = self.session.open(datagram)
            except udp_proto.ProtocolError:
                self.dropped += 1
                continue
            if packet_type == udp_proto.TYPE_KEEPALIVE_ACK:
                self._last_from_server = time.monotonic()
                udp.setblocking(False)
                return
        raise UdpTunnelError(
            "UDP traffic to the tunnel server {}:{} is blocked. "
            "Please, check your firewall or network.".format(self.remote[0], self.setup.port)
        )

    def start(self) -> None:
        self._local.setblocking(False)
        self._thread = threading.Thread(
            target=self._run, name=f"GorizontVS-UDP-{self.server_address[1]}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        local = self._local
        udp = typing.cast(socket.socket, self._udp)
        control = typing.cast(ssl.SSLSocket, self._control)
        started = time.monotonic()
        last_keepalive = time.monotonic()
        lost_logged = False
        interval = self.setup.keepalive

        with selectors.DefaultSelector() as sel:
            sel.register(local, selectors.EVENT_READ, "local")
            sel.register(udp, selectors.EVENT_READ, "udp")
            sel.register(control, selectors.EVENT_READ, "control")
            try:
                while not self.stop_flag.is_set():
                    for key, _ in sel.select(TICK):
                        if key.data == "local":
                            self._from_local(local, udp)
                        elif key.data == "udp":
                            self._from_tunnel(local, udp)
                        elif not self._control_alive(control):
                            log.info("UDP tunnel %s: control connection closed", self.server_address)
                            self.stop_flag.set()
                            break

                    now = time.monotonic()
                    if now - last_keepalive >= interval:
                        last_keepalive = now
                        with contextlib.suppress(OSError):
                            udp.send(self.session.seal(udp_proto.TYPE_KEEPALIVE))
                    silent = now - self._last_from_server
                    if silent > interval * LOST_AFTER_INTERVALS and not lost_logged:
                        lost_logged = True
                        log.warning("UDP tunnel %s: no answer from tunnel for %.0fs", self.server_address, silent)
                    elif silent <= interval * LOST_AFTER_INTERVALS:
                        lost_logged = False
                    if self.timeout and self.peer is None and now - started > self.timeout:
                        log.info("UDP tunnel %s: no local traffic within %ss, stopping", self.server_address, self.timeout)
                        self.stop_flag.set()
            except Exception as e:
                log.error("UDP tunnel %s failed: %s", self.server_address, e)
                self.status = ForwardState.TUNNEL_ERROR
                self.stop_flag.set()
        self._finish()

    def _from_local(self, local: socket.socket, udp: socket.socket) -> None:
        for _ in range(64):
            try:
                data, addr = local.recvfrom(udp_proto.MAX_DATAGRAM)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as e:
                log.debug("UDP tunnel local recv: %s", e)
                return
            if self.peer != addr:
                if self.peer is not None:
                    log.info("UDP tunnel %s: local peer changed %s -> %s", self.server_address, self.peer, addr)
                self.peer = addr
                self.status = ForwardState.TUNNEL_PROCESSING
            if len(data) > self.setup.mtu and not self._mtu_warned:
                self._mtu_warned = True
                log.warning(
                    "UDP tunnel %s: datagram of %d bytes exceeds tunnel mtu %d (will be fragmented)",
                    self.server_address, len(data), self.setup.mtu,
                )
            try:
                udp.send(self.session.seal(udp_proto.TYPE_DATA, data))
                self.sent += len(data)
            except (BlockingIOError, InterruptedError):
                return  
            except OSError as e:
                log.debug("UDP tunnel send: %s", e)
                return

    def _from_tunnel(self, local: socket.socket, udp: socket.socket) -> None:
        for _ in range(64):
            try:
                datagram = udp.recv(udp_proto.MAX_DATAGRAM)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as e:  
                log.debug("UDP tunnel recv: %s", e)
                return
            try:
                packet_type, payload, _newest = self.session.open(datagram)
            except udp_proto.ProtocolError:
                self.dropped += 1
                continue
            self._last_from_server = time.monotonic()
            if packet_type == udp_proto.TYPE_DATA:
                if self.peer is None:
                    continue  
                with contextlib.suppress(BlockingIOError, InterruptedError, OSError):
                    local.sendto(payload, self.peer)
                    self.received += len(payload)
            elif packet_type == udp_proto.TYPE_CLOSE:
                log.info("UDP tunnel %s: closed by tunnel server", self.server_address)
                self.stop_flag.set()
                return

    def _control_alive(self, control: ssl.SSLSocket) -> bool:
        try:
            data = control.recv(1024)
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
            return True
        except OSError:
            return False
        return bool(data)

    def check(self) -> bool:
        return self.status != ForwardState.TUNNEL_ERROR and not self.stop_flag.is_set()

    @property
    def stoppable(self) -> bool:
        return True

    def stop(self) -> None:
        with self._lock:
            if self.stop_flag.is_set() and self._closed:
                return
            self.stop_flag.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)
        if thread is None or not thread.is_alive():
            self._finish()

    def shutdown(self) -> None:  
        self.stop()

    def server_close(self) -> None: 
        self.stop()

    def _finish(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._udp is not None and hasattr(self, "session"):
            with contextlib.suppress(Exception):
                self._udp.send(self.session.seal(udp_proto.TYPE_CLOSE))
        log.debug(
            "UDP tunnel %s stopped: sent=%d received=%d dropped=%d",
            self.server_address if hasattr(self, "server_address") else "?",
            self.sent, self.received, self.dropped,
        )
        self._close_sockets()

    def _close_sockets(self) -> None:
        for sock in (self._control, self._udp, self._local):
            if sock is not None:
                with contextlib.suppress(Exception):
                    sock.close()


def forward_udp(
    remote: typing.Tuple[str, int],
    ticket: str,
    timeout: int = 0,
    local_port: int = 0,
    check_certificate: bool = True,
    use_ipv6: bool = False,
) -> UdpForwarder:
    from src.transport import tunnel as tunnel_mod 

    fu = UdpForwarder(
        remote=remote,
        ticket=ticket,
        timeout=timeout,
        local_port=local_port,
        check_certificate=check_certificate,
        use_ipv6=use_ipv6,
    )
    with tunnel_mod._ACTIVE_LOCK: 
        tunnel_mod._ACTIVE_FORWARD_SERVERS.add(fu)  
    fu.start()
    return fu
