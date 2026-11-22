"""Paths and endpoints for jarvis-studio. Override any of them with env vars."""

import os
from pathlib import Path


def _path(var: str, default: str) -> Path:
    return Path(os.environ.get(var, default)).expanduser()


STEMDECK_URL = os.environ.get("JARVIS_STEMDECK_URL", "http://127.0.0.1:8000").rstrip("/")
AI_MODELS = _path("JARVIS_AI_MODELS", "~/ai-models")
OUT_ROOT = _path("JARVIS_STUDIO_OUT", "~/Music/Jarvis")

ACESTEP_DIR = AI_MODELS / "ACE-Step-1.5"
MOSS_DIR = AI_MODELS / "MOSS-TTS-Nano"
BASIC_PITCH = AI_MODELS / "basic-pitch" / ".venv" / "bin" / "basic-pitch"
MSCORE = os.environ.get("JARVIS_MSCORE", "mscore3")

GUITARIX_PORT = int(os.environ.get("JARVIS_GUITARIX_PORT", "7000"))
OBS_HOST = os.environ.get("JARVIS_OBS_HOST", "127.0.0.1")
OBS_PORT = int(os.environ.get("JARVIS_OBS_PORT", "4455"))
OBS_PASSWORD = os.environ.get("JARVIS_OBS_PASSWORD", "")

STATE_DIR = _path("JARVIS_STUDIO_STATE", "~/.local/state/jarvis-studio")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def out_dir(*parts: str) -> Path:
    d = OUT_ROOT.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_." else "_" for c in text).strip()
    return (keep or "song")[:80]
