"""The five studio workflows. Each returns a short plain-text report.

1. practice      - voice-controlled practice track (StemDeck mix + rubberband + Guitarix)
2. to_daw        - stems into an Audacity project and an Ardour session
3. karaoke       - instrumental + lyrics files (FreeShow-ready), optional OBS recording
4. ai_produce    - StemDeck -> ACE-Step new backing -> MOSS spoken intro -> master + video
5. sheet_music   - stems -> basic-pitch MIDI -> MuseScore PDF
"""

import json
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path

from . import ardour, audacity, audio, guitarix, obs
from . import stemdeck as sd
from .config import (ACESTEP_DIR, BASIC_PITCH, MOSS_DIR, MSCORE, SCRIPTS_DIR, STEMDECK_URL,
                     out_dir, safe_name)


def _song_dir(job: dict, kind: str) -> Path:
    return out_dir(kind, safe_name(job.get("title") or job["job_id"]))


# 1 ------------------------------------------------------------------------
def practice(song: str, mute: list[str] | None = None, solo: list[str] | None = None,
             section: str | None = None, start: float | None = None, end: float | None = None,
             speed: float = 1.0, semitones: int = 0, click: bool = False, count_in: int = 0,
             loop: bool = True, play: bool = True, guitar_preset: str | None = None) -> str:
    job = sd.ensure_song(song)
    names = sd.stem_names(job)
    gains = {n: 1.0 for n in names}
    if solo:
        gains = {n: (1.0 if n in solo else 0.0) for n in names}
    for n in mute or []:
        if n not in gains:
            raise ValueError(f"Unknown part '{n}'. Parts: {', '.join(names)}")
        gains[n] = 0.0
    if section:
        start, end = sd.section_bounds(job, section)
    d = _song_dir(job, "practice")
    if solo:
        parts_tag = "solo-" + "-".join(n for n, g in gains.items() if g > 0)
    elif 0 in gains.values():
        parts_tag = "no-" + "-".join(n for n, g in gains.items() if g == 0)
    else:
        parts_tag = "full"
    tag = "-".join(filter(None, [
        parts_tag,
        section and safe_name(section).replace(" ", ""),
        speed != 1.0 and f"{int(round(speed * 100))}pct",
        semitones and f"{semitones:+d}st"]))
    raw = sd.render_mix(job, d / f"{tag}.raw.wav", gains, semitones, start, end, click, count_in)
    final = audio.change_speed(raw, d / f"{tag}.wav", speed)
    if final != raw:
        raw.unlink(missing_ok=True)
    else:
        final = raw.rename(d / f"{tag}.wav")
    report = [f"Practice track for '{job['title']}' ({job.get('key')}, {job.get('bpm')} BPM): {final}"]
    if guitar_preset:
        report.append(guitarix.start())
        report.append(guitarix.set_preset(guitar_preset))
    if play:
        report.append(audio.play(final, loop=loop))
    return "\n".join(report)


# 2 ------------------------------------------------------------------------
def to_daw(song: str, daw: str = "both", open_gui: bool = True) -> str:
    job = sd.ensure_song(song)
    d = _song_dir(job, "sessions")
    stems = sd.download_stems(job, d / "stems")
    name = safe_name(job.get("title") or job["job_id"]).replace(" ", "_")
    report = [f"Stems for '{job['title']}' in {d / 'stems'}"]
    if daw in ("audacity", "both"):
        try:
            report.append(audacity.build_project(stems, d / f"{name}.aup3"))
        except Exception as e:  # keep going to Ardour
            report.append(f"Audacity step failed: {e}")
    if daw in ("ardour", "both"):
        report.append(ardour.build_session(stems, d / "ardour" / name, name,
                                           job.get("bpm"), open_gui=open_gui))
    return "\n".join(report)


# 3 ------------------------------------------------------------------------
def _lrc_to_plain(lrc: str) -> str:
    return "\n".join(re.sub(r"^\[[0-9:.]+\]\s*", "", ln) for ln in lrc.splitlines()).strip()


def karaoke(song: str, keep_backing_vocals: bool = False, semitones: int = 0,
            record: bool = False, open_ui: bool = True) -> str:
    job = sd.ensure_song(song)
    d = _song_dir(job, "karaoke")
    gains = {n: 1.0 for n in sd.stem_names(job)}
    gains["vocals"] = 0.0
    if keep_backing_vocals and "vocals" in gains:
        gains["vocals"] = 0.25
    track = sd.render_mix(job, d / "instrumental.wav", gains, semitones)
    report = [f"Karaoke backing track: {track}"]
    lyr = sd.lyrics(job) or sd.lookup_lyrics(job)
    synced = plain = None
    if isinstance(lyr, dict):
        synced = lyr.get("synced") or lyr.get("user_synced")
        plain = lyr.get("plain")
        if not synced and isinstance(lyr.get("lines"), list):
            synced = "\n".join(
                f"[{int(l['start'] // 60):02d}:{l['start'] % 60:05.2f}] {l.get('text', '')}"
                for l in lyr["lines"] if isinstance(l, dict) and "start" in l)
    if synced:
        (d / "lyrics.lrc").write_text(synced)
        plain = plain or _lrc_to_plain(synced)
    if plain:
        # FreeShow: Import > Text file; blank lines separate slides.
        (d / "lyrics-freeshow.txt").write_text(re.sub(r"\n{2,}", "\n\n", plain) + "\n")
        report.append(f"Lyrics: {d / 'lyrics.lrc' if synced else ''} {d / 'lyrics-freeshow.txt'}".strip())
    else:
        report.append("No lyrics found online for this song (it may be instrumental or untitled).")
    if open_ui:
        webbrowser.open(STEMDECK_URL)
        report.append(f"StemDeck opened at {STEMDECK_URL}: choose Mode > Karaoke for the scrolling lyrics.")
    if record:
        report.append(obs.start())
        report.append(obs.record("start"))
    return "\n".join(report)


# 4 ------------------------------------------------------------------------
def _venv_python(root: Path) -> str:
    return str(root / ".venv" / "bin" / "python")


def speak(text: str, dest: Path, voice: str | None = None) -> Path:
    """MOSS-TTS-Nano voice clone. `voice` is a short WAV of the voice to copy."""
    voice = voice or str(MOSS_DIR / "assets" / "audio" / "en_2.wav")
    audio.run([_venv_python(MOSS_DIR), str(MOSS_DIR / "infer_onnx.py"),
               "--prompt-audio-path", voice, "--text", text,
               "--output-audio-path", str(dest)], timeout=600)
    return dest


def generate_music(caption: str, out: Path, duration: float = 30, lyrics: str | None = None,
                   task: str = "text2music", src_audio: Path | None = None,
                   strength: float = 0.5, bpm: int | None = None, key: str | None = None,
                   time_signature: str | None = None, language: str | None = None,
                   seed: int | None = None) -> Path:
    args = {"out_dir": str(out), "caption": caption, "lyrics": lyrics, "task": task,
            "duration": duration, "strength": strength,
            "src_audio": str(src_audio) if src_audio else None, "bpm": bpm, "key": key,
            "time_signature": time_signature, "language": language, "seed": seed}
    from .images import heavy_job  # one heavy AI job at a time (songs and images)
    with heavy_job("song"):
        proc = audio.run([_venv_python(ACESTEP_DIR), str(SCRIPTS_DIR / "acestep_generate.py"),
                          json.dumps(args)], timeout=7200)
    for line in proc.stdout.splitlines():
        if line.startswith("OUTPUT "):
            return Path(line[7:].strip())
    raise RuntimeError("ACE-Step finished without an output file")


def make_song(caption: str, title: str | None = None, lyrics: str | None = None,
              duration: float = 60, bpm: int | None = None, key: str | None = None,
              language: str | None = None, seed: int | None = None,
              add_to_library: bool = True, play: bool = False) -> str:
    """Write a new song with ACE-Step 1.5 (vocals when lyrics are given, else instrumental),
    save it under ~/Music/Jarvis/songs/<title>.wav and, by default, add it to the StemDeck
    library so every music and video workflow can use it."""
    if not caption.strip():
        raise ValueError("Describe the song (genre, mood, instruments, voice).")
    if not 10 <= float(duration) <= 600:
        raise ValueError("duration must be 10-600 seconds")
    title = title or caption.split(",")[0][:40]
    tmp = out_dir("generated", "raw")
    raw = generate_music(caption, tmp, duration, lyrics or None, bpm=bpm, key=key,
                         language=language, seed=seed)
    dest = out_dir("songs") / f"{safe_name(title)}.wav"
    n = 2
    while dest.exists():
        dest = dest.with_name(f"{safe_name(title)} ({n}).wav")
        n += 1
    shutil.move(str(raw), dest)
    (dest.with_suffix(".txt")).write_text(
        f"Title: {title}\nPrompt: {caption}\nBPM: {bpm or 'auto'}  Key: {key or 'auto'}\n"
        f"Length: {duration:.0f} s\nSeed: {seed if seed is not None else 'random'}\n\n"
        f"Lyrics:\n{lyrics or '[Instrumental]'}\n")
    report = [f"New song '{title}' ({'vocals' if lyrics else 'instrumental'}, {duration:.0f} s): {dest}"]
    if add_to_library:
        job = sd.wait(sd.submit(str(dest)))
        report.append(f"Added to the library as '{job['title']}' ({job.get('key')}, {job.get('bpm')} BPM, "
                      f"parts: {', '.join(sd.stem_names(job))}). It's ready for practice, karaoke and videos.")
    if play:
        report.append(audio.play(dest))
    return "\n".join(report)


def ai_produce(song: str, style: str, keep: list[str] | None = None, strength: float = 0.45,
               intro_text: str | None = None, voice: str | None = None,
               cover_image: str | None = None, make_video: bool = True) -> str:
    """Re-imagine a song: StemDeck keeps the parts in `keep` (default vocals), ACE-Step
    makes a new backing in `style` from the rest, MOSS adds a spoken intro."""
    job = sd.ensure_song(song)
    keep = keep or ["vocals"]
    d = _song_dir(job, "productions")
    names = sd.stem_names(job)
    backing_src = sd.render_mix(job, d / "original-backing.wav",
                                {n: (0.0 if n in keep else 1.0) for n in names})
    kept = sd.render_mix(job, d / "kept-parts.wav", {n: (1.0 if n in keep else 0.0) for n in names})
    dur = audio.duration(backing_src)
    new_backing = generate_music(style, d / "acestep", duration=dur, task="cover",
                                 src_audio=backing_src, strength=strength)
    inputs, delays = [(new_backing, 0.9), (kept, 1.0)], [0.0, 0.0]
    report = [f"New backing ({style}): {new_backing}"]
    offset = 0.0
    if intro_text:
        intro = speak(intro_text, d / "intro.wav", voice)
        offset = audio.duration(intro) + 0.5
        inputs.append((intro, 1.0))
        delays = [offset, offset, 0.0]
        report.append(f"Spoken intro: {intro}")
    master = audio.mix_files(inputs, d / "master.wav", delays)
    report.append(f"Master (-14 LUFS): {master}")
    if make_video:
        video = d / "video.mp4"
        if cover_image and Path(cover_image).expanduser().is_file():
            img = ["-loop", "1", "-i", str(Path(cover_image).expanduser())]
            vf = ("[1:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720[bg];"
                  "[0:a]showwaves=s=1280x200:mode=cline:colors=white[w];"
                  "[bg][w]overlay=0:520,format=yuv420p[v]")
        else:
            img = ["-f", "lavfi", "-i", "color=c=0x101820:s=1280x720"]
            vf = ("[0:a]showwaves=s=1280x300:mode=cline:colors=0xf4b740[w];"
                  "[1:v][w]overlay=0:210,format=yuv420p[v]")
        audio.run(["ffmpeg", "-y", "-v", "error", "-i", str(master), *img,
                   "-filter_complex", vf, "-map", "[v]", "-map", "0:a", "-shortest",
                   "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k",
                   str(video)], timeout=1800)
        report.append(f"Video (open in Kdenlive to edit further): {video}")
    return "\n".join(report)


# 5 ------------------------------------------------------------------------
def sheet_music(song: str, parts: list[str] | None = None, pdf: bool = True) -> str:
    job = sd.ensure_song(song)
    presence = job.get("stem_presence") or {}
    parts = parts or [p for p in ("vocals", "bass", "guitar", "piano")
                      if presence.get(p, 100) >= 20]
    if not parts:
        raise ValueError("No clearly present melodic parts; pass parts=[...] to force.")
    d = _song_dir(job, "sheet-music")
    stems = sd.download_stems(job, d / "stems", parts)
    midi_dir = d / "midi"
    midi_dir.mkdir(exist_ok=True)
    audio.run([str(BASIC_PITCH), "--save-midi", str(midi_dir), *map(str, stems.values())],
              timeout=3600)
    report = [f"MIDI for {', '.join(parts)} in {midi_dir}"]
    if pdf:
        for mid in sorted(midi_dir.glob("*.mid")):
            out = d / (mid.stem.replace("_basic_pitch", "") + ".pdf")
            try:
                audio.run(["xvfb-run", "-a", MSCORE, "-o", str(out), str(mid)]
                          if shutil.which("xvfb-run") else
                          [MSCORE, "-platform", "offscreen", "-o", str(out), str(mid)], timeout=300)
                report.append(f"Score: {out}")
            except Exception as e:
                report.append(f"MuseScore could not engrave {mid.name}: {e}")
    return "\n".join(report)
