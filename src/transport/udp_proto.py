from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import os
import struct
import typing

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

VERSION: typing.Final[int] = 1
COMMAND: typing.Final[bytes] = b"UDPO"

TYPE_DATA: typing.Final[int] = 0x01
TYPE_KEEPALIVE: typing.Final[int] = 0x02
TYPE_KEEPALIVE_ACK: typing.Final[int] = 0x03
TYPE_CLOSE: typing.Final[int] = 0x04
TYPES: typing.Final[frozenset[int]] = frozenset({TYPE_DATA, TYPE_KEEPALIVE, TYPE_KEEPALIVE_ACK, TYPE_CLOSE})

SESSION_ID_LENGTH: typing.Final[int] = 8
KEY_LENGTH: typing.Final[int] = 32
TAG_LENGTH: typing.Final[int] = 16
HEADER = struct.Struct("!BB8sQ")
HEADER_LENGTH: typing.Final[int] = HEADER.size
OVERHEAD: typing.Final[int] = HEADER_LENGTH + TAG_LENGTH

DEFAULT_MTU: typing.Final[int] = 1500 - 20 - 8 - OVERHEAD
MAX_DATAGRAM: typing.Final[int] = 65507
DEFAULT_KEEPALIVE: typing.Final[int] = 10
REPLAY_WINDOW: typing.Final[int] = 1024
MAX_SETUP_LENGTH: typing.Final[int] = 4096

LABEL_C2S: typing.Final[bytes] = b"C2S\x00"
LABEL_S2C: typing.Final[bytes] = b"S2C\x00"

Direction = typing.Literal["c2s", "s2c"]


class ProtocolError(ValueError):
    """Malformed setup or datagram."""


class AuthenticationError(ProtocolError):
    """Datagram did not authenticate (wrong key, session or tampered)."""


class ReplayError(ProtocolError):
    """Datagram counter already seen or too old."""


@dataclasses.dataclass(frozen=True)
class SessionSetup:
    port: int
    session_id: bytes
    key_c2s: bytes
    key_s2c: bytes
    keepalive: int = DEFAULT_KEEPALIVE
    mtu: int = DEFAULT_MTU

    @staticmethod
    def generate(port: int, keepalive: int = DEFAULT_KEEPALIVE, mtu: int = DEFAULT_MTU) -> "SessionSetup":
        return SessionSetup(
            port=port,
            session_id=os.urandom(SESSION_ID_LENGTH),
            key_c2s=os.urandom(KEY_LENGTH),
            key_s2c=os.urandom(KEY_LENGTH),
            keepalive=keepalive,
            mtu=mtu,
        )

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "v": VERSION,
                "port": self.port,
                "sid": self.session_id.hex(),
                "c2s": base64.b64encode(self.key_c2s).decode(),
                "s2c": base64.b64encode(self.key_s2c).decode(),
                "keepalive": self.keepalive,
                "mtu": self.mtu,
            },
            separators=(",", ":"),
        ).encode()

    def encode_response(self) -> bytes:
        body = self.to_json()
        return b"OK" + struct.pack("!H", len(body)) + body

    @staticmethod
    def from_json(raw: bytes) -> "SessionSetup":
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ProtocolError(f"invalid setup JSON: {e}") from None
        if not isinstance(data, dict):
            raise ProtocolError("setup must be a JSON object")
        if data.get("v") != VERSION:
            raise ProtocolError(f"unsupported UDP tunnel version {data.get('v')!r}")
        try:
            port = int(data["port"])
            session_id = bytes.fromhex(str(data["sid"]))
            key_c2s = base64.b64decode(str(data["c2s"]), validate=True)
            key_s2c = base64.b64decode(str(data["s2c"]), validate=True)
            keepalive = int(data.get("keepalive", DEFAULT_KEEPALIVE))
            mtu = int(data.get("mtu", DEFAULT_MTU))
        except (KeyError, TypeError, ValueError, binascii.Error) as e:
            raise ProtocolError(f"invalid setup field: {e}") from None
        if not 0 < port < 65536:
            raise ProtocolError("invalid UDP port")
        if len(session_id) != SESSION_ID_LENGTH:
            raise ProtocolError("invalid session id")
        if len(key_c2s) != KEY_LENGTH or len(key_s2c) != KEY_LENGTH or key_c2s == key_s2c:
            raise ProtocolError("invalid session keys")
        if not 1 <= keepalive <= 300:
            raise ProtocolError("invalid keepalive interval")
        if not 256 <= mtu <= MAX_DATAGRAM - OVERHEAD:
            raise ProtocolError("invalid mtu")
        return SessionSetup(port, session_id, key_c2s, key_s2c, keepalive, mtu)


class ReplayWindow:

    def __init__(self, size: int = REPLAY_WINDOW) -> None:
        self.size = size
        self.highest = 0
        self.bitmap = 0 

    def check(self, counter: int) -> bool:
        if counter <= 0:
            return False
        if counter > self.highest:
            return True
        offset = self.highest - counter
        if offset >= self.size:
            return False
        return not (self.bitmap >> offset) & 1

    def update(self, counter: int) -> bool:
        if counter > self.highest:
            shift = counter - self.highest
            self.bitmap = ((self.bitmap << shift) | 1) & ((1 << self.size) - 1) if shift < self.size else 1
            self.highest = counter
            return True
        self.bitmap |= 1 << (self.highest - counter)
        return False


class Session:

    def __init__(self, setup: SessionSetup, role: typing.Literal["client", "server"]) -> None:
        self.setup = setup
        self.role = role
        c2s, s2c = AESGCM(setup.key_c2s), AESGCM(setup.key_s2c)
        if role == "client":
            self._send_aead, self._send_label = c2s, LABEL_C2S
            self._recv_aead, self._recv_label = s2c, LABEL_S2C
        else:
            self._send_aead, self._send_label = s2c, LABEL_S2C
            self._recv_aead, self._recv_label = c2s, LABEL_C2S
        self._counter = 0
        self.window = ReplayWindow()

    @property
    def session_id(self) -> bytes:
        return self.setup.session_id

    def seal(self, packet_type: int, payload: bytes = b"") -> bytes:
        if packet_type not in TYPES:
            raise ProtocolError(f"unknown packet type {packet_type}")
        if len(payload) > MAX_DATAGRAM - OVERHEAD:
            raise ProtocolError("payload too large")
        self._counter += 1
        if self._counter >= 1 << 64:
            raise ProtocolError("counter exhausted, session must be renewed")
        header = HEADER.pack(VERSION, packet_type, self.session_id, self._counter)
        nonce = self._send_label + struct.pack("!Q", self._counter)
        return header + self._send_aead.encrypt(nonce, payload, header)

    @staticmethod
    def peek_session_id(datagram: bytes) -> typing.Optional[bytes]:
        if len(datagram) < OVERHEAD or datagram[0] != VERSION:
            return None
        return datagram[2 : 2 + SESSION_ID_LENGTH]

    def open(self, datagram: bytes) -> tuple[int, bytes, bool]:
        if len(datagram) < OVERHEAD:
            raise ProtocolError("datagram too short")
        version, packet_type, session_id, counter = HEADER.unpack_from(datagram)
        if version != VERSION:
            raise ProtocolError(f"unsupported version {version}")
        if packet_type not in TYPES:
            raise ProtocolError(f"unknown packet type {packet_type}")
        if session_id != self.session_id:
            raise AuthenticationError("wrong session")
        if not self.window.check(counter):
            raise ReplayError(f"replayed or too old counter {counter}")
        header = datagram[:HEADER_LENGTH]
        nonce = self._recv_label + struct.pack("!Q", counter)
        try:
            payload = self._recv_aead.decrypt(nonce, datagram[HEADER_LENGTH:], header)
        except Exception: 
            raise AuthenticationError("datagram does not authenticate") from None
        return packet_type, payload, self.window.update(counter)


def read_setup_response(
    recv_exact: typing.Callable[[int], bytes],
    recv_more: typing.Optional[typing.Callable[[], bytes]] = None,
) -> SessionSetup:
    status = recv_exact(2)
    if status != b"OK":
        if recv_more is not None:
            try:
                status += recv_more()
            except Exception:
                pass
        raise ConnectionError(status.decode(errors="ignore"))
    (length,) = struct.unpack("!H", recv_exact(2))
    if not 0 < length <= MAX_SETUP_LENGTH:
        raise ProtocolError("invalid setup length")
    return SessionSetup.from_json(recv_exact(length))
