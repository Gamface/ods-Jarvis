"""Shared video helpers: output folders, sizes, hardware encoding, probing, audio analysis."""

import functools
import json
import os
import subprocess
from pathlib import Path

import numpy as np

from ..audio import run
from ..config import _path, safe_name

VIDEO_OUT = _path("JARVIS_VIDEO_OUT", "~/Videos/Jarvis")
TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"
VAAPI_DEVICE = os.environ.get("JARVIS_VAAPI_DEVICE", "/dev/dri/renderD128")
FONT = os.environ.get("JARVIS_VIDEO_FONT", "Inter")
FPS = 30

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".mts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


def vout(*parts: str) -> Path:
    d = VIDEO_OUT.joinpath(*[safe_name(p) for p in parts])
    d.mkdir(parents=True, exist_ok=True)
    return d


def size_of(aspect: str) -> tuple[int, int]:
    if aspect not in SIZES:
        raise ValueError(f"aspect must be one of {', '.join(SIZES)}")
    return SIZES[aspect]


@functools.cache
def vaapi_ok() -> bool:
    """True when the Intel GPU can encode H.264 (much faster than the CPU)."""
    if not Path(VAAPI_DEVICE).exists():
        return False
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=0.2:s=320x240",
                        "-vaapi_device", VAAPI_DEVICE, "-vf", "format=nv12,hwupload",
                        "-c:v", "h264_vaapi", "-f", "null", "-"], capture_output=True)
    return p.returncode == 0


def encoder(final: bool = True) -> tuple[list[str], str, list[str]]:
    """(args before inputs, filter suffix for the last video label, codec args)."""
    if vaapi_ok():
        return (["-vaapi_device", VAAPI_DEVICE], "format=nv12,hwupload",
                ["-c:v", "h264_vaapi", "-rc_mode", "CQP", "-qp", "20" if final else "16"])
    return ([], "format=yuv420p",
            ["-c:v", "libx264", "-preset", "veryfast" if final else "ultrafast",
             "-crf", "20" if final else "16"])


AUDIO_OUT = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]


def render(inputs: list[str], graph: str, vlabel: str, dest: Path, alabel: str | None = None,
           extra: list[str] | None = None, final: bool = True, timeout: int = 7200) -> Path:
    """Run ffmpeg with a filter graph. `inputs` is the raw input args (-i ... etc).
    `vlabel` is the graph's final video label; `alabel` an audio label or map spec like '1:a'."""
    pre, tail, codec = encoder(final)
    graph = f"{graph};[{vlabel}]{tail}[vout]" if graph else f"[0:v]{tail}[vout]"
    cmd = ["ffmpeg", "-y", "-v", "error", *pre, *inputs, "-filter_complex", graph, "-map", "[vout]"]
    if alabel:
        cmd += ["-map", f"[{alabel}]" if ":" not in alabel else alabel, *AUDIO_OUT]
    cmd += [*codec, "-r", str(FPS), *(extra or []), "-movflags", "+faststart", str(dest)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(cmd, timeout=timeout)
    return dest


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,width,height,r_frame_rate",
               "-of", "json", str(path)]).stdout
    info = json.loads(out)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    fps = 30.0
    if v and v.get("r_frame_rate", "0/0") != "0/0":
        n, d = v["r_frame_rate"].split("/")
        fps = float(n) / float(d or 1) if float(d or 1) else 30.0
    return {"duration": float(info.get("format", {}).get("duration") or 0),
            "width": v and v.get("width"), "height": v and v.get("height"),
            "fps": fps if 1 < fps < 241 else 30.0, "has_video": v is not None, "has_audio": a}


def pcm(path: Path, sr: int = 8000, start: float | None = None, dur: float | None = None) -> np.ndarray:
    """Mono float32 samples of any audio/video file."""
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _envelope(x: np.ndarray, sr: int, hop: float) -> np.ndarray:
    n = max(1, int(sr * hop))
    frames = len(x) // n
    if frames == 0:
        return np.zeros(1)
    return np.sqrt((x[:frames * n].reshape(frames, n) ** 2).mean(axis=1))


def loudest_window(path: Path, length: float) -> float:
    """Start time (s) of the loudest `length`-second stretch."""
    sr, hop = 8000, 0.25
    env = _envelope(pcm(path, sr), sr, hop)
    win = max(1, int(length / hop))
    if len(env) <= win:
        return 0.0
    sums = np.convolve(env, np.ones(win), "valid")
    return float(np.argmax(sums) * hop)


def onset_envelope(path: Path, sr: int = 8000, hop: float = 0.01) -> np.ndarray:
    env = _envelope(pcm(path, sr), sr, hop)
    flux = np.maximum(np.diff(np.log1p(env * 100), prepend=0), 0)
    return flux


def estimate_tempo(path: Path) -> float:
    """Rough BPM from onset autocorrelation (70-180 BPM)."""
    hop = 0.01
    flux = onset_envelope(path, hop=hop)
    flux = flux - flux.mean()
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lags = np.arange(len(ac)) * hop
    ok = (lags > 60 / 180) & (lags < 60 / 70)
    if not ok.any():
        return 120.0
    lag = lags[ok][np.argmax(ac[ok])]
    bpm = 60 / lag
    return round(bpm * 2 if bpm < 85 else bpm, 1)  # prefer the faster of two octave guesses


def beat_phase(path: Path, bpm: float, hop: float = 0.01) -> float:
    """Offset (s) of the first beat that best lines up with the onsets."""
    flux = onset_envelope(path, hop=hop)
    period = 60 / bpm
    best, best_score = 0.0, -1.0
    for off in np.arange(0, period, hop):
        idx = (np.arange(off, len(flux) * hop, period) / hop).astype(int)
        idx = idx[idx < len(flux)]
        score = flux[idx].sum()
        if score > best_score:
            best, best_score = float(off), score
    return best


def find_offset(recording: Path, reference: Path, search: float = 45.0) -> float:
    """Seconds into `recording` where `reference` starts (cross-correlation)."""
    sr = 4000
    rec = pcm(recording, sr, dur=search + 30)
    ref = pcm(reference, sr, dur=30)
    if not len(rec) or not len(ref):
        raise RuntimeError("Could not read audio for alignment")
    n = 1 << int(np.ceil(np.log2(len(rec) + len(ref))))
    rec_f = np.fft.rfft(rec - rec.mean(), n)
    ref_f = np.fft.rfft(ref - ref.mean(), n)
    corr = np.fft.irfft(rec_f * np.conj(ref_f), n)[:len(rec)]
    return float(np.argmax(corr) / sr)


def media_in(sources) -> list[Path]:
    """Expand folders / paths / lists into sorted video+image files."""
    if isinstance(sources, (str, Path)):
        sources = [s.strip() for s in str(sources).split(",")] if "," in str(sources) else [sources]
    files = []
    for s in sources:
        p = Path(str(s)).expanduser()
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT | IMAGE_EXT)
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"Not found: {p}")
    if not files:
        raise ValueError("No video or image files found")
    return files


def ass_path(p: Path) -> str:
    """Path quoted for ffmpeg's ass/subtitles filters."""
    return "'" + str(p).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:") + "'"
