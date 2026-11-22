"""Run with ACE-Step's own venv python:  acestep_generate.py <json-args>

Args (JSON): {"out_dir", "caption", "lyrics", "task": "text2music"|"cover"|"repaint",
              "src_audio", "duration", "strength", "seed", "steps",
              "bpm", "key", "time_signature", "language"}
Prints one line: OUTPUT <path>
"""

import json
import os
import sys

args = json.loads(sys.argv[1])
ROOT = os.environ.get("ACESTEP_ROOT", os.path.expanduser("~/ai-models/ACE-Step-1.5"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from acestep.handler import AceStepHandler  # noqa: E402
from acestep.inference import GenerationConfig, GenerationParams, generate_music  # noqa: E402
from acestep.llm_inference import LLMHandler  # noqa: E402

dit = AceStepHandler()
msg, ok = dit.initialize_service(project_root=ROOT, config_path="acestep-v15-turbo",
                                 device="auto", offload_to_cpu=False)
if not ok:
    sys.exit(f"ACE-Step init failed: {msg}")

task = args.get("task", "text2music")
params = dict(
    task_type=task,
    thinking=False,
    caption=args.get("caption", ""),
    lyrics=args.get("lyrics") or "[Instrumental]",
    instrumental=not args.get("lyrics"),
    duration=args.get("duration"),
    inference_steps=int(args.get("steps", 8)),
    seed=int(args.get("seed") if args.get("seed") is not None else -1),
)
if args.get("bpm"):
    params["bpm"] = int(args["bpm"])
if args.get("key"):
    params["keyscale"] = str(args["key"])          # e.g. "C major", "A minor"
if args.get("time_signature"):
    params["timesignature"] = str(args["time_signature"])  # "4" for 4/4, "3" for 3/4
if args.get("language") and args.get("lyrics"):
    params["vocal_language"] = str(args["language"])  # e.g. "en", "es"
if task in ("cover", "repaint") and args.get("src_audio"):
    params["src_audio"] = args["src_audio"]
    params["audio_cover_strength"] = float(args.get("strength", 0.5))
result = generate_music(dit, LLMHandler(), params=GenerationParams(**params),
                        config=GenerationConfig(batch_size=1, audio_format="wav"),
                        save_dir=args["out_dir"])
if not result.success:
    sys.exit(f"ACE-Step failed: {result.status_message}")
print("OUTPUT", result.audios[0]["path"])
