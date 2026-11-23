from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Iterable


RESOLUTION_NATIVE = "native"
RESOLUTION_PRESETS: tuple[str, ...] = ("1280x720", "1920x1080", "2560x1440", "3840x2160")

FPS_PRESETS: tuple[int, ...] = (30, 60, 90, 120, 144, 240)
FPS_MIN, FPS_MAX = 10, 480

BITRATE_MIN_KBPS, BITRATE_MAX_KBPS = 500, 150_000

DISPLAY_MODES: tuple[str, ...] = ("fullscreen", "borderless", "windowed")
AUDIO_CONFIGS: tuple[str, ...] = ("stereo", "5.1-surround", "7.1-surround")
CAPTURE_KEYS_MODES: tuple[str, ...] = ("never", "fullscreen", "always")
VIDEO_DECODERS: tuple[str, ...] = ("auto", "software", "hardware")
VIDEO_CODECS: tuple[str, ...] = ("auto", "H.264", "HEVC", "AV1")

_RESOLUTION_RE = re.compile(r"^\s*(\d{3,5})\s*[xX×]\s*(\d{3,5})\s*$")


@dataclass(frozen=True)
class GorizontVSSettings:
    resolution: str = ""           
    fps: int = 0                    
    bitrate: int = 0               
    display_mode: str = "fullscreen"
    vsync: bool = True
    frame_pacing: bool = False
    audio_config: str = "stereo"
    mute_host_audio: bool = True   
    mute_on_focus_loss: bool = False
    absolute_mouse: bool = True    
    capture_system_keys: str = "never"
    performance_overlay: bool = False
    detect_network_blocking: bool = True
    keep_awake: bool = True
    video_decoder: str = "auto"
    video_codec: str = "auto"     
    hdr: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def to_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _choice(value: Any, allowed: Iterable[str], default: str) -> str:
    text = str(value or "").strip()
    for item in allowed:
        if text.lower() == item.lower():
            return item
    return default


def parse_resolution(value: Any) -> tuple[int, int] | None:
    match = _RESOLUTION_RE.match(str(value or ""))
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    return width, height


def normalize_resolution(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == RESOLUTION_NATIVE:
        return RESOLUTION_NATIVE
    parsed = parse_resolution(text)
    return f"{parsed[0]}x{parsed[1]}" if parsed else ""


def normalize_fps(value: Any) -> int:
    fps = _to_int(value, 0)
    if fps <= 0:
        return 0
    return max(FPS_MIN, min(FPS_MAX, fps))


def normalize_bitrate(value: Any) -> int:
    kbps = _to_int(value, 0)
    if kbps <= 0:
        return 0
    return max(BITRATE_MIN_KBPS, min(BITRATE_MAX_KBPS, kbps))


def normalize_gorizontvs_settings(raw: Any) -> GorizontVSSettings:
    data = raw if isinstance(raw, dict) else {}
    defaults = GorizontVSSettings()
    return GorizontVSSettings(
        resolution=normalize_resolution(data.get("resolution", defaults.resolution)),
        fps=normalize_fps(data.get("fps", defaults.fps)),
        bitrate=normalize_bitrate(data.get("bitrate", defaults.bitrate)),
        display_mode=_choice(data.get("display_mode"), DISPLAY_MODES, defaults.display_mode),
        vsync=to_bool(data.get("vsync"), defaults.vsync),
        frame_pacing=to_bool(data.get("frame_pacing"), defaults.frame_pacing),
        audio_config=_choice(data.get("audio_config"), AUDIO_CONFIGS, defaults.audio_config),
        mute_host_audio=to_bool(data.get("mute_host_audio"), defaults.mute_host_audio),
        mute_on_focus_loss=to_bool(data.get("mute_on_focus_loss"), defaults.mute_on_focus_loss),
        absolute_mouse=to_bool(data.get("absolute_mouse"), defaults.absolute_mouse),
        capture_system_keys=_choice(data.get("capture_system_keys"), CAPTURE_KEYS_MODES, defaults.capture_system_keys),
        performance_overlay=to_bool(data.get("performance_overlay"), defaults.performance_overlay),
        detect_network_blocking=to_bool(data.get("detect_network_blocking"), defaults.detect_network_blocking),
        keep_awake=to_bool(data.get("keep_awake"), defaults.keep_awake),
        video_decoder=_choice(data.get("video_decoder"), VIDEO_DECODERS, defaults.video_decoder),
        video_codec=_choice(data.get("video_codec"), VIDEO_CODECS, defaults.video_codec),
        hdr=to_bool(data.get("hdr"), defaults.hdr),
    )

def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(GorizontVSSettings))


def to_qml(settings: GorizontVSSettings) -> dict[str, Any]:
    return {_camel(key): value for key, value in settings.to_dict().items()}


def from_qml(values: Any) -> GorizontVSSettings:
    data = values if isinstance(values, dict) else {}
    return normalize_gorizontvs_settings({_snake(str(key)): value for key, value in data.items()})

TOGGLE_FLAGS: tuple[tuple[str, str, bool], ...] = (
    ("vsync", "vsync", False),
    ("frame_pacing", "frame-pacing", False),
    ("mute_host_audio", "audio-on-host", True),
    ("mute_on_focus_loss", "mute-on-focus-loss", False),
    ("absolute_mouse", "absolute-mouse", False),
    ("performance_overlay", "performance-overlay", False),
    ("keep_awake", "keep-awake", False),
    ("hdr", "hdr", False),
)

CHOICE_FLAGS: tuple[tuple[str, str], ...] = (
    ("audio_config", "audio-config"),
    ("capture_system_keys", "capture-system-keys"),
    ("video_decoder", "video-decoder"),
)

UNMAPPED_SETTINGS: tuple[str, ...] = ("detect_network_blocking",)


def gorizontvs_extra_args(settings: GorizontVSSettings) -> list[str]:
    args: list[str] = []
    data = settings.to_dict()
    for key, flag, invert in TOGGLE_FLAGS:
        enabled = bool(data[key]) != invert
        args.append(f"--{flag}" if enabled else f"--no-{flag}")
    for key, flag in CHOICE_FLAGS:
        args += [f"--{flag}", str(data[key])]
    return args


def apply_gorizontvs_options(options: dict[str, Any], settings: GorizontVSSettings) -> dict[str, Any]:
    result = dict(options)

    parsed = parse_resolution(settings.resolution)
    if parsed is not None:
        result["width"], result["height"] = parsed
    if settings.fps > 0:
        result["fps"] = settings.fps
    if settings.bitrate > 0:
        result["bitrate"] = settings.bitrate
    if settings.video_codec != "auto":
        result["codec"] = settings.video_codec

    result["display_mode"] = settings.display_mode
    result["extra_args"] = gorizontvs_extra_args(settings)
    return result
