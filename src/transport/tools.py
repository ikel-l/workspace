from __future__ import annotations

import base64
import contextlib
import logging
import os
import os.path
import secrets
import socket
import stat
import sys
import tempfile
import threading
import time
import typing

from cryptography.hazmat.backends import default_backend  # type: ignore
from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore

from src.transport.consts import PUBLIC_KEY
from src.utils.types import AwaitableTask, RemovableFile

log = logging.getLogger(__name__)

try:
    import psutil # type: ignore

    def process_iter(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        return psutil.process_iter(*args, **kwargs)

except ImportError:
    def process_iter() -> typing.Any:
        return []



_unlink_files: typing.List[RemovableFile] = []
_awaitable_tasks: typing.List[AwaitableTask] = []
_execute_before_exit: typing.List[typing.Callable[[], None]] = []
_registry_lock = threading.RLock()


class LaunchScope:

    __slots__ = ("label", "files", "tasks", "callbacks", "launch_code", "processes", "on_failure")

    def __init__(self, label: str = "") -> None:
        self.label = label
        self.files: typing.List[RemovableFile] = []
        self.tasks: typing.List[AwaitableTask] = []
        self.callbacks: typing.List[typing.Callable[[], None]] = []
        self.launch_code = ""
        self.processes: typing.List[typing.Any] = []
        self.on_failure: typing.Optional[typing.Callable[[str], None]] = None

    def __repr__(self) -> str:
        return f"<LaunchScope {self.label or id(self)}>"


_scope_local = threading.local()


def current_scope() -> typing.Optional[LaunchScope]:
    return getattr(_scope_local, "scope", None)


@contextlib.contextmanager
def launch_scope(label: str = "") -> typing.Iterator[LaunchScope]:
    scope = LaunchScope(label)
    previous = current_scope()
    _scope_local.scope = scope
    try:
        yield scope
    finally:
        _scope_local.scope = previous


def _claim(registry: typing.List[typing.Any], owned: typing.List[typing.Any]) -> typing.List[typing.Any]:
    claimed = []
    for item in owned:
        try:
            registry.remove(item)
        except ValueError:
            continue
        claimed.append(item)
    owned.clear()
    return claimed

sys_fs_enc = sys.getfilesystemencoding() or "mbcs"


def _user_tag() -> str:
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return str(getuid())
    return "".join(ch for ch in (os.environ.get("USERNAME") or "user") if ch.isalnum()) or "user"


def private_temp_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), f"gorizontvs-{_user_tag()}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    if os.name != "nt":
        st = os.lstat(path)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) & 0o077
        ):
            log.warning("Unsafe temp directory %s; using a fresh private one", path)
            path = tempfile.mkdtemp(prefix="gorizontvs-")
    return path


def _safe_filename(filename: str) -> str:
    name = os.path.basename(filename.replace("\\", "/"))
    if not name or name in {".", ".."}:
        raise ValueError(f"invalid temp file name: {filename!r}")
    return name


def save_temp_file(content: str, filename: typing.Optional[str] = None) -> str:
    if filename is None:
        filename = secrets.token_hex(8) + ".gorizontvs"
    filepath = os.path.join(private_temp_dir(), _safe_filename(filename))
    with contextlib.suppress(FileNotFoundError):
        os.unlink(filepath)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(filepath, flags, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    log.info("Saved temp file: %s", filepath)
    return filepath


def read_temp_file(filename: str) -> typing.Optional[str]:
    try:
        filepath = os.path.join(private_temp_dir(), _safe_filename(filename))
    except Exception:  # noqa: BLE001
        return None
    try:
        with open(filepath, "r") as f:
            return f.read()
    except Exception:
        return None


def test_server(host: str, port: str | int, timeout: int = 4) -> bool:
    try:
        sock = socket.create_connection((host, int(port)), timeout)
        sock.close()
    except Exception:
        return False
    return True


def find_application(
    application_name: str, extra_path: typing.Optional[str] = None
) -> typing.Optional[str]:
    search_path = os.environ.get("PATH", "").split(os.pathsep)
    if extra_path:
        search_path.append(extra_path)

    for path in search_path:
        file_name = os.path.join(path, application_name)
        if os.path.isfile(file_name) and os.access(file_name, os.X_OK):
            return file_name

    if sys.platform == "darwin" and application_name.lower() == "x2goclient":
        app_candidates = (
            "/Applications/x2goclient.app/Contents/MacOS/x2goclient",
            "/Applications/X2Go Client.app/Contents/MacOS/x2goclient",
            os.path.expanduser(
                "~/Applications/x2goclient.app/Contents/MacOS/x2goclient"
            ),
            os.path.expanduser(
                "~/Applications/X2Go Client.app/Contents/MacOS/x2goclient"
            ),
        )
        for file_name in app_candidates:
            if os.path.isfile(file_name) and os.access(file_name, os.X_OK):
                log.info("Found macOS application bundle executable: %s", file_name)
                return file_name

    return None


def gethostname() -> str:
    hostname = socket.gethostname()
    log.info("Hostname: %s", hostname)
    return hostname


def register_for_delayed_deletion(filename: str, early_stage: bool = False) -> None:
    log.debug("Queued for deletion (%s): %s", "early" if early_stage else "late", filename)
    item = RemovableFile(filename, early_stage)
    scope = current_scope()
    with _registry_lock:
        _unlink_files.append(item)
        if scope is not None:
            scope.files.append(item)


def _unlink_paths(files: typing.Iterable[RemovableFile]) -> None:
    for f in files:
        try:
            os.unlink(f.path)
        except Exception as e:
            log.debug("Could not delete %s: %s", f.path, e)


def unlink_files(
    early_stage: bool = False,
    *,
    delay: bool = True,
    scope: typing.Optional[LaunchScope] = None,
) -> None:
    stage = "early" if early_stage else "late"
    log.debug("Unlinking files (%s stage)", stage)
    with _registry_lock:
        source = _unlink_files if scope is None else scope.files
        files_to_unlink = [f for f in source if f.early_stage == early_stage]
    if not files_to_unlink:
        return
    if delay:
        time.sleep(1 + 2 * (1 + int(early_stage)))
    with _registry_lock:
        if scope is not None:
            scope.files[:] = [f for f in scope.files if f.early_stage != early_stage]
            files_to_unlink = _claim(_unlink_files, files_to_unlink)
        else:
            for f in files_to_unlink:
                with contextlib.suppress(ValueError):
                    _unlink_files.remove(f)
    _unlink_paths(files_to_unlink)


def add_task_to_wait(task: typing.Any, wait_subprocesses: bool = False) -> None:
    log.debug("Added task to wait: %s (subprocesses=%s)", task, wait_subprocesses)
    item = AwaitableTask(task, wait_subprocesses)
    scope = current_scope()
    with _registry_lock:
        _awaitable_tasks.append(item)
        if scope is not None:
            scope.tasks.append(item)


def _children_of(pid: int) -> list[typing.Any]:
    try:
        process_list = process_iter(attrs=("ppid",))
    except TypeError:
        process_list = process_iter()
    return [p for p in process_list if p.ppid() == pid]


def wait_for_tasks(scope: typing.Optional[LaunchScope] = None) -> None:
    with _registry_lock:
        snapshot = list(_awaitable_tasks if scope is None else scope.tasks)
    log.debug("Waiting for %d task(s)", len(snapshot))
    for awaitable in snapshot:
        try:
            if hasattr(awaitable.task, "join"):
                awaitable.task.join()
            elif hasattr(awaitable.task, "wait"):
                awaitable.task.wait()

            if awaitable.wait_subprocesses and hasattr(awaitable.task, "pid"):
                for sp in _children_of(awaitable.task.pid):
                    sp.wait()
        except Exception as e:
            log.error("Error waiting for task: %s", e)
    with _registry_lock:
        for awaitable in snapshot:
            with contextlib.suppress(ValueError):
                _awaitable_tasks.remove(awaitable)
            if scope is not None:
                with contextlib.suppress(ValueError):
                    scope.tasks.remove(awaitable)


def _terminate(tasks: typing.Iterable[AwaitableTask]) -> None:
    for awaitable in tasks:
        task = awaitable.task
        try:
            if hasattr(task, "terminate"):
                task.terminate()
            elif hasattr(task, "kill"):
                task.kill()
            elif hasattr(task, "close"):
                task.close()
        except Exception as e:
            log.debug("Could not terminate task %s: %s", task, e)

        if awaitable.wait_subprocesses and hasattr(task, "pid"):
            try:
                for sp in _children_of(task.pid):
                    with contextlib.suppress(Exception):
                        sp.terminate()
            except Exception as e:
                log.debug("Could not terminate child processes for %s: %s", task, e)


def terminate_tasks() -> None:
    with _registry_lock:
        tasks = list(_awaitable_tasks)
        _awaitable_tasks.clear()
    log.debug("Terminating %d task(s)", len(tasks))
    _terminate(tasks)


def register_execute_before_exit(fnc: typing.Callable[[], None]) -> None:
    log.debug("Registered before-exit callback: %s", fnc)
    scope = current_scope()
    with _registry_lock:
        _execute_before_exit.append(fnc)
        if scope is not None:
            scope.callbacks.append(fnc)


def _run_callbacks(callbacks: typing.Iterable[typing.Callable[[], None]]) -> None:
    for fnc in callbacks:
        try:
            fnc()
        except Exception as e:
            log.error("before-exit callback error: %s", e)


def execute_before_exit(scope: typing.Optional[LaunchScope] = None) -> None:
    with _registry_lock:
        if scope is None:
            callbacks = list(_execute_before_exit)
            _execute_before_exit.clear()
        else:
            callbacks = _claim(_execute_before_exit, scope.callbacks)
    _run_callbacks(callbacks)


class SessionResources(typing.NamedTuple):
    files: list[RemovableFile]
    tasks: list[AwaitableTask]
    callbacks: list[typing.Callable[[], None]]


def take_session_resources() -> SessionResources:
    """Atomically detach everything registered by transport scripts.

    Used on logout: the caller owns the returned resources and can release
    them in a background thread while a new session starts with empty
    registries.
    """
    with _registry_lock:
        res = SessionResources(list(_unlink_files), list(_awaitable_tasks), list(_execute_before_exit))
        _unlink_files.clear()
        _awaitable_tasks.clear()
        _execute_before_exit.clear()
    return res


def release_session_resources(res: SessionResources) -> None:
    _run_callbacks(res.callbacks)
    _terminate(res.tasks)
    _unlink_paths(res.files)


def verify_signature(script: bytes, signature: bytes) -> bool:
    public_key = serialization.load_pem_public_key(
        data=PUBLIC_KEY, backend=default_backend()
    )
    try:
        public_key.verify(  # type: ignore[union-attr]
            base64.b64decode(signature),
            script,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return False
    return True



def get_cacerts_file() -> typing.Optional[str]:
    if "CERTIFICATE_BUNDLE_PATH" in os.environ:
        return os.environ["CERTIFICATE_BUNDLE_PATH"]
    try:
        import certifi # type: ignore

        if os.path.exists(certifi.where()):
            return certifi.where()
    except Exception:
        pass
    if "linux" in sys.platform:
        for path in (
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/ssl/ca-bundle.pem",
        ):
            if os.path.exists(path):
                return path
    return None

def start_clipboard(
    port: int,
    key: str,
    mode: str,
    max_size: int,
    launch_id: str = "",
    host: str = "127.0.0.1",
    focus: typing.Any = None,
) -> typing.Any:

    from src.transport import clipboard_agent, clipboard_focus, clipboard_proto, clipboard_qt

    try:
        secret = base64.b64decode(str(key).encode("ascii"), validate=True)
    except Exception as e:
        raise ValueError("the clipboard key is not valid base64") from e
    if len(secret) != clipboard_proto.SECRET_SIZE:
        raise ValueError("the clipboard key has a wrong size")
    policy = clipboard_proto.Policy.of(mode, max_size)
    if not policy.enabled:
        raise ValueError("the clipboard is not enabled for this connection")
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError("wrong clipboard port")

    def audit(line: str) -> None:
        log.info("%s", line)

    if focus is not None and not isinstance(focus, clipboard_focus.FocusChannel):
        raise ValueError("focus is not what clipboard_focus() returned")

    def note(line: str) -> None:
        log.info("clipboard client%s: %s", " launch {}".format(launch_id) if launch_id else "", line)

    backend: typing.Any = clipboard_qt.QtClipboardBackend()
    if focus is not None:
        backend = clipboard_focus.gate(backend, focus, note)

    agent = clipboard_agent.ClipboardAgent(
        clipboard_proto.ROLE_CLIENT,
        secret,
        policy,
        backend,
        launch_id=str(launch_id or ""),
        log=audit,
    )
    endpoint = clipboard_agent.ClientEndpoint(agent, (host, port))
    endpoint.start()
    log.info(
        "clipboard client launch %s: started, mode %s, local port %s, %s",
        launch_id or "-",
        policy.mode,
        port,
        "follows the focus of the stream window" if focus is not None else "whatever window has the focus",
    )
    return endpoint

def clipboard_focus() -> typing.Any:
    from src.transport import clipboard_focus as focus_mod

    def note(line: str) -> None:
        log.info("clipboard client: %s", line)

    channel = focus_mod.FocusChannel(log=note)
    channel.start()
    return channel

def is_macos() -> bool:
    return "darwin" in sys.platform

def is_linux() -> bool:
    return "linux" in sys.platform

def is_windows() -> bool:
    # NB: "win" in "darwin" is True, so a substring check is wrong on macOS.
    return sys.platform.startswith("win")


saveTempFile = save_temp_file
readTempFile = read_temp_file
testServer = test_server
findApp = find_application
addTaskToWait = add_task_to_wait
addFileToUnlink = register_for_delayed_deletion