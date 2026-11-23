import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import typing

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION: typing.Final[int] = 1

MAGIC: typing.Final[bytes] = b'GVSCLIP1'
RANDOM_SIZE: typing.Final[int] = 16
SECRET_SIZE: typing.Final[int] = 32
TAG_SIZE: typing.Final[int] = 16
HEADER: typing.Final[struct.Struct] = struct.Struct('>I')

DEFAULT_MAX_SIZE: typing.Final[int] = 1024 * 1024
HARD_MAX_SIZE: typing.Final[int] = 8 * 1024 * 1024
HELLO_MAX_RECORD: typing.Final[int] = 512

HANDSHAKE_TIMEOUT: typing.Final[float] = 5.0

HELLO: typing.Final[int] = 1
DATA: typing.Final[int] = 2
CLEAR: typing.Final[int] = 3
ERROR: typing.Final[int] = 4
PING: typing.Final[int] = 5

READY: typing.Final[int] = 6

FORMAT_TEXT: typing.Final[int] = 1

ROLE_CLIENT: typing.Final[str] = 'client'
ROLE_HOST: typing.Final[str] = 'host'

MODE_DISABLED: typing.Final[str] = 'disabled'
MODE_CLIENT_TO_HOST: typing.Final[str] = 'client_to_host'
MODE_HOST_TO_CLIENT: typing.Final[str] = 'host_to_client'
MODE_BIDIRECTIONAL: typing.Final[str] = 'bidirectional'
MODES: typing.Final[tuple[str, ...]] = (
    MODE_DISABLED,
    MODE_CLIENT_TO_HOST,
    MODE_HOST_TO_CLIENT,
    MODE_BIDIRECTIONAL,
)


class ProtocolError(Exception):
    """The peer broke the protocol, or the connection is gone."""


class AuthenticationError(ProtocolError):
    """The peer does not know the secret of the launch."""


class Policy(typing.NamedTuple):
    mode: str = MODE_DISABLED
    max_size: int = DEFAULT_MAX_SIZE

    @staticmethod
    def of(mode: typing.Any, max_size: typing.Any = DEFAULT_MAX_SIZE) -> 'Policy':
        mode = mode if isinstance(mode, str) and mode in MODES else MODE_DISABLED
        try:
            size = int(max_size)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return Policy(MODE_DISABLED, DEFAULT_MAX_SIZE)
        return Policy(mode, min(size, HARD_MAX_SIZE))

    @property
    def client_to_host(self) -> bool:
        return self.mode in (MODE_CLIENT_TO_HOST, MODE_BIDIRECTIONAL)

    @property
    def host_to_client(self) -> bool:
        return self.mode in (MODE_HOST_TO_CLIENT, MODE_BIDIRECTIONAL)

    @property
    def enabled(self) -> bool:
        return self.client_to_host or self.host_to_client

    def allows_sending(self, role: str) -> bool:
        return self.client_to_host if role == ROLE_CLIENT else self.host_to_client

    def allows_receiving(self, role: str) -> bool:
        return self.host_to_client if role == ROLE_CLIENT else self.client_to_host

    def restricted_by(self, other: 'Policy') -> 'Policy':
        c2h = self.client_to_host and other.client_to_host
        h2c = self.host_to_client and other.host_to_client
        if c2h and h2c:
            mode = MODE_BIDIRECTIONAL
        elif c2h:
            mode = MODE_CLIENT_TO_HOST
        elif h2c:
            mode = MODE_HOST_TO_CLIENT
        else:
            mode = MODE_DISABLED
        return Policy(mode, min(self.max_size, other.max_size))


def text_digest(text: typing.Optional[str]) -> typing.Optional[str]:
    if not text:
        return None
    return hashlib.sha256(text.encode('utf-8', errors='surrogatepass')).hexdigest()


def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    return hmac.new(prk, info + b'\x01', hashlib.sha256).digest()[:length]


def derive_keys(secret: bytes, client_random: bytes, host_random: bytes) -> tuple[bytes, bytes]:
    if len(secret) != SECRET_SIZE or len(client_random) != RANDOM_SIZE or len(host_random) != RANDOM_SIZE:
        raise ValueError('wrong size of the key material')
    salt = client_random + host_random
    return (
        _hkdf(secret, salt, b'gorizontvs clipboard v1 client to host'),
        _hkdf(secret, salt, b'gorizontvs clipboard v1 host to client'),
    )


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    left = size
    while left:
        try:
            chunk = sock.recv(min(left, 65536))
        except socket.timeout:
            raise ProtocolError('the peer is silent') from None
        except OSError as e:
            raise ProtocolError('connection lost: {}'.format(e)) from None
        if not chunk:
            raise ProtocolError('connection closed')
        chunks.append(chunk)
        left -= len(chunk)
    return b''.join(chunks)


class Channel:

    def __init__(self, sock: socket.socket, send_key: bytes, recv_key: bytes) -> None:
        self._sock = sock
        self._send = AESGCM(send_key)
        self._recv = AESGCM(recv_key)
        self._send_counter = 0
        self._recv_counter = 0
        self._send_lock = threading.Lock()
        self.max_record = HELLO_MAX_RECORD

    @staticmethod
    def _nonce(counter: int) -> bytes:
        return counter.to_bytes(12, 'big')

    def send(self, kind: int, payload: bytes = b'') -> None:
        with self._send_lock:
            header = HEADER.pack(1 + len(payload) + TAG_SIZE)
            sealed = self._send.encrypt(self._nonce(self._send_counter), bytes((kind,)) + payload, header)
            self._send_counter += 1
            try:
                self._sock.sendall(header + sealed)
            except OSError as e:
                raise ProtocolError('connection lost: {}'.format(e)) from None

    def recv(self) -> tuple[int, bytes]:
        header = read_exact(self._sock, HEADER.size)
        (size,) = HEADER.unpack(header)
        if size < 1 + TAG_SIZE or size > self.max_record:
            raise ProtocolError('record of {} bytes is not acceptable'.format(size))
        sealed = read_exact(self._sock, size)
        try:
            plain = self._recv.decrypt(self._nonce(self._recv_counter), sealed, header)
        except InvalidTag:
            raise AuthenticationError('record is not authentic') from None
        self._recv_counter += 1
        return plain[0], plain[1:]

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def record_limit(policy: Policy) -> int:
    return 1 + 1 + policy.max_size + TAG_SIZE


def handshake(
    sock: socket.socket,
    role: str,
    secret: bytes,
    policy: Policy,
    timeout: float = HANDSHAKE_TIMEOUT,
) -> tuple[Channel, Policy]:
    if role not in (ROLE_CLIENT, ROLE_HOST):
        raise ValueError('unknown role')
    sock.settimeout(timeout)
    mine = os.urandom(RANDOM_SIZE)
    try:
        if role == ROLE_CLIENT:
            sock.sendall(MAGIC + mine)
            greeting = read_exact(sock, len(MAGIC) + RANDOM_SIZE)
        else:
            greeting = read_exact(sock, len(MAGIC) + RANDOM_SIZE)
            if greeting[: len(MAGIC)] == MAGIC:
                sock.sendall(MAGIC + mine)
    except OSError as e:
        raise ProtocolError('connection lost: {}'.format(e)) from None
    if greeting[: len(MAGIC)] != MAGIC:
        raise ProtocolError('not a clipboard channel')
    theirs = greeting[len(MAGIC) :]

    if role == ROLE_CLIENT:
        c2h, h2c = derive_keys(secret, mine, theirs)
        channel = Channel(sock, send_key=c2h, recv_key=h2c)
    else:
        c2h, h2c = derive_keys(secret, theirs, mine)
        channel = Channel(sock, send_key=h2c, recv_key=c2h)

    channel.send(
        HELLO,
        json.dumps({'v': VERSION, 'role': role, 'mode': policy.mode, 'max': policy.max_size}).encode('ascii'),
    )
    kind, payload = channel.recv()
    if kind != HELLO:
        raise ProtocolError('the peer did not start with HELLO')
    try:
        hello = json.loads(payload.decode('ascii'))
        if not isinstance(hello, dict):
            raise ValueError('not an object')
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError('malformed HELLO') from None
    if hello.get('v') != VERSION:
        raise ProtocolError('the peer speaks version {} of the protocol'.format(hello.get('v')))
    if hello.get('role') != (ROLE_HOST if role == ROLE_CLIENT else ROLE_CLIENT):
        raise ProtocolError('the peer has the same role')

    agreed = policy.restricted_by(Policy.of(hello.get('mode'), hello.get('max')))
    channel.max_record = record_limit(agreed)
    sock.settimeout(None)
    return channel, agreed


def encode_text(text: str) -> bytes:
    return bytes((FORMAT_TEXT,)) + text.encode('utf-8')


def decode_text(payload: bytes) -> str:
    if not payload or payload[0] != FORMAT_TEXT:
        raise ProtocolError('unknown clipboard format')
    try:
        text = payload[1:].decode('utf-8')
    except UnicodeDecodeError:
        raise ProtocolError('clipboard text is not valid UTF-8') from None
    if not text or '\x00' in text:
        raise ProtocolError('clipboard text is empty or holds a NUL character')
    return text
