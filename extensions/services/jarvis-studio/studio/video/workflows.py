"""The five video workflows. Each returns a short plain-text report with file paths.

1. music_video   - StemDeck song -> visualizer (per-stem lanes / spectrum / waves), title, lyrics
2. performance   - OBS recording + studio backing -> aligned, studio audio, lyrics
                   (perform_start / perform_finish drive OBS and playback for you)
3. auto_edit     - folder of clips/photos + song -> beat-cut video + OpenTimelineIO for Kdenlive
4. shorts        - any video or song -> 9:16 / 1:1 / 16:9 clips with captions and thumbnails
5. narrated      - script -> MOSS-TTS voice, slides or photos, Whisper captions, ducked music
   captions      - Whisper subtitles (.srt) for any video, optionally burned in
"""

import json
import random
import time
from pathlib import Path

from .. import audio, obs
from .. import stemdeck as sd
from ..config import STATE_DIR, safe_name
from . import captions as cap
from . import graphics as gfx
from .common import (AUDIO_EXT, FPS, IMAGE_EXT, VIDEO_EXT, beat_phase, estimate_tempo,
                     find_offset, loudest_window, media_in, probe, render, size_of, vout,
                     ass_path, encoder)
from ..audio import run

# Every segment gets identical colour metadata so the concat demuxer never re-initialises filters.
SAME = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
STEM_ORDER = ["vocals", "guitar", "piano", "other", "bass", "drums"]


def _is_media(s: str, exts: set) -> bool:
    p = Path(str(s)).expanduser()
    return p.is_file() and p.suffix.lower() in exts


def _title_overlay(inputs: list, graph: list, vin: str, title_png: Path, n: int,
                   start: float = 0.0, hold: float = 3.6) -> str:
    """Add a fading title card on top of label `vin`; returns the new label."""
    inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{start + hold + 1:.2f}", "-i", str(title_png)]
    graph.append(f"[{n}:v]format=rgba,fade=t=in:st={start:.2f}:d=0.6:alpha=1,"
                 f"fade=t=out:st={start + hold:.2f}:d=0.8:alpha=1[ttl]")
    graph.append(f"[{vin}][ttl]overlay=0:0:eof_action=pass[vt]")
    return "vt"


def _window(job: dict, section: str | None, start: float | None, end: float | None,
            max_len: float | None = None, mix: Path | None = None) -> tuple[float, float]:
    if section:
        start, end = sd.section_bounds(job, section)
    start = start or 0.0
    end = end or float(job.get("duration") or 0) or None
    if max_len and (end is None or end - start > max_len):
        if mix is not None and not section and start == 0:
            start = loudest_window(mix, max_len)
        end = start + max_len
    return start, end


# 1 ------------------------------------------------------------------------
def music_video(song: str, style: str = "stems", aspect: str = "16:9", title: str | None = None,
                subtitle: str | None = None, background: str | None = None, lyrics: bool = True,
                section: str | None = None, start: float | None = None, end: float | None = None,
                audio_file: str | None = None, out: Path | None = None) -> str:
    """style: stems (a lane per part), spectrum (CQT bars), waves (one big waveform)."""
    if style not in ("stems", "spectrum", "waves"):
        raise ValueError("style must be stems, spectrum or waves")
    job = sd.ensure_song(song)
    w, h = size_of(aspect)
    name = job.get("title") or job["job_id"]
    d = out or vout("music-videos", name)
    full = d / "song-mix.wav"
    if audio_file:
        full = Path(audio_file).expanduser()
    elif not full.exists():
        sd.render_mix(job, full, {n: 1.0 for n in sd.stem_names(job)})
    start, end = _window(job, section, start, end)
    end = end or probe(full).get("duration")
    dur = end - start
    meta = " · ".join(str(x) for x in (job.get("key"), job.get("bpm") and f"{job['bpm']} BPM") if x)

    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(full)]
    graph, n = [], 1
    if background and Path(background).expanduser().is_file():
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(Path(background).expanduser())]
        graph.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                     f"gblur=sigma=14,eq=brightness=-0.18:saturation=1.1,setsar=1,fps={FPS}[bg]")
    else:
        inputs += ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x1b1233:c1=0x0b2a3c:c2=0x2a1424:"
                   f"nb_colors=3:speed=0.004:r={FPS}"]
        graph.append(f"[{n}:v]format=yuv420p,setsar=1[bg]")
    n += 1
    v = "bg"

    lrc = cap.song_lyrics_lrc(job) if lyrics else None
    cues = cap.cues_from_lrc(lrc, clip=(start, end)) if lrc else []
    bottom = 0.78 if cues else 0.92

    if style == "stems":
        presence = job.get("stem_presence") or {}
        stems = [s for s in STEM_ORDER if s in sd.stem_names(job) and presence.get(s, 100) >= 8]
        files = sd.download_stems(job, d / "stems", stems)
        top = int(h * (0.22 if w >= h else 0.30))
        lane_h = int((h * bottom - top) / max(1, len(stems)))
        lx, lw = int(w * 0.16), int(w * 0.78)
        for i, s in enumerate(stems):
            inputs += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(files[s])]
            color = gfx.PALETTE.get(s, gfx.ACCENT).replace("#", "0x")
            graph.append(f"[{n}:a]aformat=channel_layouts=mono,dynaudnorm=f=150:g=15,showwaves=s={lw}x{lane_h}:mode=cline:"
                         f"rate={FPS}:colors={color}:scale=sqrt:draw=full,format=rgba[w{i}]")
            graph.append(f"[{v}][w{i}]overlay={lx}:{top + i * lane_h}:shortest=1[l{i}]")
            v, n = f"l{i}", n + 1
        ov = gfx.stems_overlay(title or name, meta, stems, (w, h), (top, lane_h), d / f"overlay-{aspect.replace(':', 'x')}.png")
    else:
        if style == "spectrum":
            sh = int(h * (0.42 if w >= h else 0.30))
            graph.append(f"[0:a]showcqt=s={w}x{sh}:fps={FPS}:sono_h=0:axis=0:bar_g=2:"
                         f"bar_v=12:cscheme=1|0.55|0.15|0.9|0.75|0.3,format=rgba,"
                         f"colorkey=0x000000:0.06:0.12[viz]")
            y = int(h * bottom) - sh
        else:
            sh = int(h * 0.42)
            graph.append(f"[0:a]dynaudnorm=f=150:g=15,showwaves=s={int(w * 0.84)}x{sh}:mode=cline:rate={FPS}:"
                         f"colors=0xf4b740:scale=sqrt:draw=full,format=rgba[viz]")
            y = int(h * 0.5 - sh / 2)
        x = 0 if style == "spectrum" else int(w * 0.08)
        graph.append(f"[{v}][viz]overlay={x}:{y}:shortest=1[viz2]")
        v = "viz2"
        ov = gfx.stems_overlay(title or name, meta, [], (w, h), (int(h * (0.22 if w >= h else 0.30)), 0),
                               d / f"overlay-{aspect.replace(':', 'x')}-{style}.png")
    inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(ov)]
    graph.append(f"[{v}][{n}:v]overlay=0:0:shortest=1[ov]")
    v, n = "ov", n + 1

    tag = f"{style}-{aspect.replace(':', 'x')}" + (f"-{safe_name(section)}" if section else "")
    if cues:
        ass = cap.write_ass(cues, d / f"lyrics-{tag}.ass", (w, h), "lyrics")
        cap.write_srt(cues, d / f"lyrics-{tag}.srt")
        graph.append(f"[{v}]ass={ass_path(ass)}[ly]")
        v = "ly"
    ttl = gfx.title_card(title or name, subtitle, (w, h), d / f"title-{aspect.replace(':', 'x')}.png", meta)
    v = _title_overlay(inputs, graph, v, ttl, n)
    graph.append(f"[{v}]fade=t=in:st=0:d=0.8,fade=t=out:st={max(0, dur - 1.2):.2f}:d=1.2[vf]")
    graph.append(f"[0:a]afade=t=in:st=0:d=0.3,afade=t=out:st={max(0, dur - 1.2):.2f}:d=1.2[af]")
    dest = render(inputs, ";".join(graph), "vf", d / f"{safe_name(name)}-{tag}.mp4", "af",
                  extra=["-t", f"{dur:.3f}"])
    thumb = gfx.thumbnail(gfx.grab_frame(dest, min(dur * 0.4, 20), d / f"frame-{tag}.jpg"),
                          title or name, (w, h), d / f"thumbnail-{tag}.png", subtitle or meta)
    lines = [f"Music video ({style}, {aspect}, {dur:.0f} s): {dest}", f"Thumbnail: {thumb}"]
    lines.append(f"Lyrics: {len(cues)} lines burned in" if cues else
                 "No synced lyrics found, so none shown." if lyrics else "Lyrics off.")
    return "\n".join(lines)


# 2 ------------------------------------------------------------------------
PERF_STATE = STATE_DIR / "performance.json"


def perform_start(song: str, mode: str = "karaoke", semitones: int = 0, scene: str | None = None) -> str:
    """Render the backing, start OBS recording, then play the backing on the speakers.
    mode: karaoke (no lead vocal), full (whole song), or 'no-<part>' e.g. no-guitar."""
    job = sd.ensure_song(song)
    names = sd.stem_names(job)
    gains = {n: 1.0 for n in names}
    if mode == "karaoke":
        gains["vocals"] = 0.0
    elif mode.startswith("no-"):
        gains[mode[3:]] = 0.0
    d = vout("performances", job.get("title") or job["job_id"])
    backing = sd.render_mix(job, d / f"backing-{mode}{f'-{semitones:+d}st' if semitones else ''}.wav",
                            gains, semitones)
    msgs = [obs.start()]
    if scene:
        msgs.append(obs.set_scene(scene))
    obs.record("start")
    time.sleep(1.0)
    msgs.append(audio.play(backing, loop=False))
    PERF_STATE.parent.mkdir(parents=True, exist_ok=True)
    PERF_STATE.write_text(json.dumps({"song": job["job_id"], "backing": str(backing), "mode": mode,
                                      "semitones": semitones, "started": time.time()}))
    return "\n".join(msgs + ["Recording. Perform now; say 'finish the performance' when done."])


def perform_finish(lyrics: bool = True, voice_gain: float = 1.0) -> str:
    if not PERF_STATE.exists():
        raise RuntimeError("No performance in progress (start one with perform_start).")
    st = json.loads(PERF_STATE.read_text())
    audio.stop()
    path = obs.record("stop").split(": ", 1)[-1].strip()
    PERF_STATE.unlink(missing_ok=True)
    if not path or not Path(path).exists():
        raise RuntimeError(f"OBS did not report the recording file ({path!r})")
    return performance(path, st["song"], st["backing"], lyrics, voice_gain)


def performance(recording: str, song: str, backing: str | None = None, lyrics: bool = True,
                voice_gain: float = 1.0, backing_gain: float = 0.85, aspect: str | None = None) -> str:
    """Line up an existing recording with the studio backing, mix them and add lyrics."""
    rec = Path(recording).expanduser()
    info = probe(rec)
    if not info["has_video"]:
        raise ValueError("The recording has no video stream")
    job = sd.ensure_song(song)
    d = vout("performances", job.get("title") or job["job_id"])
    if backing:
        bk = Path(backing).expanduser()
    else:
        bk = sd.render_mix(job, d / "backing-karaoke.wav",
                           {n: (0.0 if n == "vocals" else 1.0) for n in sd.stem_names(job)})
    bdur = probe(bk)["duration"]
    report = []
    if info["has_audio"]:
        off = find_offset(rec, bk)
        report.append(f"Backing found {off:.2f} s into the recording.")
    else:
        off = 0.0
        report.append("Recording has no audio; assuming the backing starts at 0 s.")
    t0 = max(0.0, off - 1.0)
    dur = min(info["duration"] - t0, bdur + (off - t0) + 1.5)
    w, h = size_of(aspect) if aspect else (info["width"], info["height"])
    w, h = w - w % 2, h - h % 2
    lead = off - t0  # backing starts this many seconds into the output
    inputs = ["-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(rec), "-i", str(bk)]
    ms = int(lead * 1000)
    graph = [f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={FPS}[v0]",
             f"[1:a]adelay={ms}|{ms},volume={backing_gain}[bk]"]
    if info["has_audio"]:
        graph.append(f"[0:a]volume={voice_gain}[me]")
        graph.append("[bk][me]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-14:TP=-1:LRA=11[a]")
    else:
        graph.append("[bk]loudnorm=I=-14:TP=-1:LRA=11[a]")
    v = "v0"
    lrc = cap.song_lyrics_lrc(job) if lyrics else None
    if lrc:
        cues = cap.cues_from_lrc(lrc, offset=lead)
        ass = cap.write_ass(cues, d / "performance-lyrics.ass", (w, h), "lyrics")
        graph.append(f"[{v}]ass={ass_path(ass)}[ly]")
        v = "ly"
        report.append(f"Lyrics: {len(cues)} lines.")
    ttl = gfx.title_card(job.get("title") or "Performance", "Live take", (w, h), d / "perf-title.png")
    v = _title_overlay(inputs, graph, v, ttl, 2, start=0.3, hold=2.4)
    graph.append(f"[{v}]fade=t=out:st={max(0, dur - 1):.2f}:d=1[vf]")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = render(inputs, ";".join(graph), "vf", d / f"performance-{stamp}.mp4", "a",
                  extra=["-t", f"{dur:.3f}"])
    report.insert(0, f"Performance video ({dur:.0f} s): {dest}")
    report.append(f"Original recording kept: {rec}")
    return "\n".join(report)


# 3 ------------------------------------------------------------------------
def _segment(src: Path, dest: Path, size: tuple[int, int], frames: int, inpoint: float = 0.0,
             kenburns: int = 0) -> Path:
    """One clip/photo -> exact-length silent segment at the edit's size and frame rate."""
    w, h = size
    if src.suffix.lower() in IMAGE_EXT:
        zin = "1+0.10*on/{f}" if kenburns % 2 == 0 else "1.10-0.10*on/{f}"
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='{zin.format(f=frames)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d={frames}:s={w}x{h}:fps={FPS},setsar=1,format=yuv420p,{SAME}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf]
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},"
              f"setsar=1,format=yuv420p,{SAME}")
        cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-ss", f"{inpoint:.3f}",
               "-i", str(src), "-vf", vf]
    cmd += ["-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "16", "-r", str(FPS), str(dest)]
    run(cmd, timeout=900)
    return dest


def _otio_time(value: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": round(value, 3)}


def _otio_clip(name: str, path: Path, start: float, dur: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "Clip.2", "name": name, "metadata": {}, "enabled": True,
            "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": _otio_time(start * rate, rate),
                             "duration": _otio_time(dur * rate, rate)},
            "effects": [], "markers": [], "active_media_reference_key": "DEFAULT_MEDIA",
            "media_references": {"DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1", "name": path.name, "metadata": {},
                "available_range": None, "available_image_bounds": None,
                "target_url": path.resolve().as_uri()}}}


def _otio_track(name: str, kind: str, children: list) -> dict:
    return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind, "metadata": {}, "enabled": True,
            "source_range": None, "effects": [], "markers": [], "children": children}


def auto_edit(clips, song: str, aspect: str = "16:9", pace: str = "medium",
              title: str | None = None, section: str | None = None, start: float | None = None,
              max_len: float | None = None, lyrics: bool = False, seed: int | None = None,
              bpm: float | None = None) -> str:
    """Cut a folder of clips/photos to the beat of a song.
    song: StemDeck title/id, or an audio file path. pace: slow (8 beats), medium (4), fast (2)."""
    files = media_in(clips)
    beats = {"slow": 8, "medium": 4, "fast": 2}.get(pace)
    if not beats:
        raise ValueError("pace must be slow, medium or fast")
    w, h = size_of(aspect)
    job = None
    if _is_media(song, AUDIO_EXT):
        music = Path(song).expanduser()
        name = title or music.stem
        s0, s1 = (start or 0.0), None
    else:
        job = sd.ensure_song(song)
        name = title or job.get("title") or job["job_id"]
        music = vout("edits", name) / "song-mix.wav"
        if not music.exists():
            sd.render_mix(job, music, {n: 1.0 for n in sd.stem_names(job)})
        s0, s1 = _window(job, section, start, None, max_len, music)
    total = probe(music)["duration"]
    s1 = min(s1 or total, total, s0 + max_len if max_len else total)
    dur = s1 - s0
    d = vout("edits", name)
    tempo = bpm or (job and job.get("bpm")) or estimate_tempo(music)
    period = 60.0 / float(tempo)
    phase = beat_phase(music, float(tempo)) if s0 == 0 else 0.0
    cut_len = period * beats
    # cut points on the beat grid, as exact frame numbers
    times = [0.0]
    t = phase + cut_len if phase > 0.25 * cut_len else cut_len
    while t < dur - cut_len * 0.5:
        times.append(t)
        t += cut_len
    times.append(dur)
    frames = [round(x * FPS) for x in times]

    rng = random.Random(seed if seed is not None else 7)
    order = files[:]
    rng.shuffle(order)
    info = {f: (probe(f) if f.suffix.lower() in VIDEO_EXT else {"duration": 0, "fps": FPS}) for f in order}
    cursor = {f: info[f]["duration"] * rng.uniform(0.05, 0.25) for f in order}
    seg_dir = d / "segments"
    seg_dir.mkdir(exist_ok=True)
    for old in seg_dir.glob("*.mp4"):
        old.unlink()
    segs, otio_v = [], []
    for i in range(len(frames) - 1):
        f = order[i % len(order)]
        nfr = frames[i + 1] - frames[i]
        seglen = nfr / FPS
        cd = info[f]["duration"]
        inp = 0.0
        if cd:
            if cursor[f] + seglen > cd:
                cursor[f] = rng.uniform(0, max(0.0, cd - seglen)) if cd > seglen else 0.0
            inp = cursor[f]
            cursor[f] += seglen + cd * 0.12
        segs.append(_segment(f, seg_dir / f"{i:04d}.mp4", (w, h), nfr, inp, i))
        rate = info[f]["fps"] if cd else FPS
        otio_v.append(_otio_clip(f.name, f, inp, seglen, rate))
    lst = d / "segments.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in segs))

    inputs = ["-f", "concat", "-safe", "0", "-i", str(lst),
              "-ss", f"{s0:.3f}", "-t", f"{dur:.3f}", "-i", str(music)]
    graph = ["[0:v]setsar=1[v0]"]
    v, n = "v0", 2
    cues = []
    if lyrics and job:
        lrc = cap.song_lyrics_lrc(job)
        cues = cap.cues_from_lrc(lrc, clip=(s0, s1)) if lrc else []
        if cues:
            ass = cap.write_ass(cues, d / "edit-lyrics.ass", (w, h), "lyrics")
            graph.append(f"[{v}]ass={ass_path(ass)}[ly]")
            v = "ly"
    meta = job and " · ".join(str(x) for x in (job.get("key"), job.get("bpm") and f"{job['bpm']} BPM") if x)
    ttl = gfx.title_card(name, None, (w, h), d / f"title-{aspect.replace(':', 'x')}.png", meta or None)
    v = _title_overlay(inputs, graph, v, ttl, n)
    graph.append(f"[{v}]fade=t=in:st=0:d=0.5,fade=t=out:st={max(0, dur - 1.2):.2f}:d=1.2[vf]")
    graph.append(f"[1:a]afade=t=in:st=0:d=0.2,afade=t=out:st={max(0, dur - 1.5):.2f}:d=1.5[af]")
    tag = f"{pace}-{aspect.replace(':', 'x')}"
    dest = render(inputs, ";".join(graph), "vf", d / f"{safe_name(name)}-edit-{tag}.mp4", "af",
                  extra=["-t", f"{dur:.3f}"])

    otio = {"OTIO_SCHEMA": "Timeline.1", "name": f"{name} ({pace} cut)", "metadata": {},
            "global_start_time": None,
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "metadata": {}, "enabled": True,
                       "source_range": None, "effects": [], "markers": [], "children": [
                           _otio_track("V1", "Video", otio_v),
                           _otio_track("A1", "Audio", [_otio_clip(music.name, music, s0, dur, FPS)])]}}
    otio_path = d / f"{safe_name(name)}-edit-{tag}.otio"
    otio_path.write_text(json.dumps(otio, indent=1))
    thumb = gfx.thumbnail(gfx.grab_frame(dest, min(dur * 0.3, 12), d / "frame.jpg"), name, (w, h),
                          d / f"thumbnail-{tag}.png", meta or None)
    return "\n".join([
        f"Beat-cut edit ({len(segs)} cuts every {beats} beats at {float(tempo):.0f} BPM, {dur:.0f} s): {dest}",
        f"Kdenlive timeline: {otio_path} (Kdenlive: File > Import > OpenTimelineIO)",
        f"Thumbnail: {thumb}",
        f"Used {len(files)} source files" + (f", {len(cues)} lyric lines" if cues else "") + "."])


# 4 ------------------------------------------------------------------------
PLATFORMS = {"shorts": "9:16", "reels": "9:16", "tiktok": "9:16", "square": "1:1",
             "instagram": "1:1", "youtube": "16:9"}


def shorts(source: str, platforms: list[str] | None = None, length: float = 30,
           start: float | None = None, section: str | None = None, caption: str = "auto",
           song: str | None = None, title: str | None = None) -> str:
    """Make social clips from a video file, or from a StemDeck song (renders a visualizer).
    caption: auto (lyrics if a song is known, else Whisper speech), lyrics, speech or none."""
    platforms = platforms or ["shorts", "square"]
    bad = [p for p in platforms if p not in PLATFORMS]
    if bad:
        raise ValueError(f"Unknown platform(s) {bad}. Choose from {', '.join(PLATFORMS)}")
    aspects = sorted({PLATFORMS[p] for p in platforms}, key=list(PLATFORMS.values()).index)
    report = []
    if not _is_media(source, VIDEO_EXT):
        # A song: render a visualizer at each aspect over the chosen window
        job = sd.ensure_song(source)
        name = title or job.get("title") or job["job_id"]
        d = vout("shorts", name)
        mix = d / "song-mix.wav"
        if not mix.exists():
            sd.render_mix(job, mix, {n: 1.0 for n in sd.stem_names(job)})
        if section is None and start is None:
            try:
                s0, s1 = sd.section_bounds(job, "chorus")
                s1 = min(s1, s0 + length)
            except Exception:
                s0 = loudest_window(mix, length)
                s1 = s0 + length
        else:
            s0, s1 = _window(job, section, start, None, length, mix)
        for a in aspects:
            r = music_video(job["job_id"], "spectrum" if a != "16:9" else "stems", a, name,
                            None, None, caption != "none", None, s0, s1, out=d)
            report.append(f"[{a} for {', '.join(p for p in platforms if PLATFORMS[p] == a)}]\n{r}")
        return "\n".join(report)

    src = Path(source).expanduser()
    info = probe(src)
    name = title or src.stem
    d = vout("shorts", name)
    length = min(length, info["duration"])
    if start is None:
        start = loudest_window(src, length) if info["has_audio"] else 0.0
    start = max(0.0, min(start, info["duration"] - length))
    job = sd.ensure_song(song) if song else None
    segs = []
    mode = caption
    if mode == "auto":
        mode = "lyrics" if job else ("speech" if info["has_audio"] else "none")
    if mode == "speech":
        clip_wav = d / "clip-audio.wav"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src),
             "-vn", "-ac", "1", "-ar", "16000", str(clip_wav)])
        segs = cap.transcribe(clip_wav)
    for a in aspects:
        w, h = size_of(a)
        tag = a.replace(":", "x")
        inputs = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src)]
        if a == "16:9" and (info["width"] or 0) >= (info["height"] or 0):
            graph = [f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[v0]"]
        else:  # blurred fill behind the fitted video
            graph = [f"[0:v]split[a][b]",
                     f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                     f"gblur=sigma=30,eq=brightness=-0.2[bgv]",
                     f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg]",
                     f"[bgv][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[v0]"]
        v = "v0"
        cues = []
        if mode == "speech" and segs:
            cues = cap.pop_cues(segs) if a != "16:9" else cap.cues_from_whisper(segs)
        elif mode == "lyrics" and job:
            lrc = cap.song_lyrics_lrc(job)
            cues = cap.cues_from_lrc(lrc, clip=(start, start + length)) if lrc else []
        if cues:
            ass = cap.write_ass(cues, d / f"captions-{tag}.ass", (w, h),
                                "pop" if (mode == "speech" and a != "16:9") else "lyrics" if mode == "lyrics" else "bottom")
            cap.write_srt(cues, d / f"captions-{tag}.srt")
            graph.append(f"[{v}]ass={ass_path(ass)}[cv]")
            v = "cv"
        graph.append(f"[{v}]fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, length - 0.5):.2f}:d=0.5[vf]")
        alabel = None
        if info["has_audio"]:
            graph.append(f"[0:a]loudnorm=I=-14:TP=-1:LRA=11,afade=t=in:st=0:d=0.2,"
                         f"afade=t=out:st={max(0, length - 0.6):.2f}:d=0.6[af]")
            alabel = "af"
        dest = render(inputs, ";".join(graph), "vf", d / f"{safe_name(name)}-{tag}-{int(length)}s.mp4",
                      alabel, extra=["-t", f"{length:.3f}"])
        thumb = gfx.thumbnail(gfx.grab_frame(dest, length * 0.35, d / f"frame-{tag}.jpg"), name, (w, h),
                              d / f"thumbnail-{tag}.png")
        report.append(f"[{a} for {', '.join(p for p in platforms if PLATFORMS[p] == a)}] {dest}\n"
                      f"  thumbnail {thumb}" + (f", {len(cues)} captions" if cues else ""))
    report.insert(0, f"Clip {start:.1f}-{start + length:.1f} s of {src.name}, -14 LUFS:")
    return "\n".join(report)


# 5 ------------------------------------------------------------------------
def _paragraphs(script: str) -> list[str]:
    is_file = "\n" not in script and len(script) < 255 and Path(script).expanduser().is_file()
    text = Path(script).expanduser().read_text() if is_file else script
    if "\n" not in text and "\\n" in text:  # model sent escaped newlines ("\\n\\n") as text
        text = text.replace("\\r", "").replace("\\n", "\n")
    paras = [" ".join(x.split()) for x in text.split("\n\n") if x.strip()]
    if len(paras) == 1:  # one block: split every ~2 sentences
        import re
        sents = re.split(r"(?<=[.!?])\s+", paras[0])
        paras = [" ".join(sents[i:i + 2]) for i in range(0, len(sents), 2)]
    return paras


def narrated(script: str, title: str, images=None, voice: str | None = None, music: str | None = None,
             aspect: str = "16:9", music_gain: float = 0.35, voice_speed: float = 1.0) -> str:
    """Script (text or .txt path) -> a voice-over video. One slide per paragraph: your photos
    (Ken Burns) or generated text slides. music: StemDeck song (instrumental used), an audio
    file, or none. voice_speed: 1.0 normal, 1.2 faster, 0.85 slower (pitch stays the same).
    Captions come from the script and are burned in and saved as .srt."""
    from ..workflows import speak
    paras = _paragraphs(script)
    if not paras:
        raise ValueError("The script is empty")
    voice_speed = float(voice_speed or 1.0)
    if not 0.5 <= voice_speed <= 2.0:
        raise ValueError("voice_speed must be between 0.5 and 2.0")
    w, h = size_of(aspect)
    d = vout("narrated", title)
    pics = media_in(images) if images else []
    pics = [p for p in pics if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT]

    gap, intro = 0.45, 3.0
    voice_parts, lens = [], []
    for i, para in enumerate(paras):
        raw = speak(para, d / f"voice-{i:02d}.raw.wav", voice)
        # Same format as the silence clips (48 kHz mono): the concat step needs every part to
        # match, or stereo clips get read as mono and play at half speed.
        wav = d / f"voice-{i:02d}.wav"
        speed = [] if abs(voice_speed - 1.0) < 1e-3 else ["-af", f"atempo={voice_speed:.3f}"]
        run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), *speed, "-ar", "48000", "-ac", "1",
             "-c:a", "pcm_s16le", str(wav)])
        voice_parts.append(wav)
        lens.append(probe(wav)["duration"])
    # narration track: intro silence, then each paragraph followed by a short gap
    concat = d / "voice.txt"
    sil = d / "silence.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", f"{gap}", str(sil)])
    lead = d / "lead.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", f"{intro}", str(lead)])
    parts = [lead] + [x for p in voice_parts for x in (p, sil)]
    concat.write_text("".join(f"file '{p}'\n" for p in parts))
    narration = d / "narration.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-ar", "48000", "-ac", "1", str(narration)])
    total = probe(narration)["duration"] + 0.8

    seg_dir = d / "segments"
    seg_dir.mkdir(exist_ok=True)
    segs = [_segment(gfx.slide(title, (w, h), seg_dir / "title.png", "Jarvis Studio"),
                     seg_dir / "0000.mp4", (w, h), round(intro * FPS))]
    for i, (para, ln) in enumerate(zip(paras, lens)):
        frames = round((ln + gap + (0.8 if i == len(paras) - 1 else 0)) * FPS)
        src = pics[i % len(pics)] if pics else gfx.slide(para, (w, h), seg_dir / f"slide-{i:02d}.png",
                                                        f"{i + 1} / {len(paras)}", i)
        segs.append(_segment(src, seg_dir / f"{i + 1:04d}.mp4", (w, h), frames, 0.0, i))
    lst = d / "segments.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in segs))

    # The script is known, so caption with its exact words, timed to each voice clip.
    cues, t = [], intro
    for para, ln in zip(paras, lens):
        cues += cap.cues_from_text(para, t + 0.05, ln - 0.1)
        t += ln + gap
    srt = cap.write_srt(cues, d / f"{safe_name(title)}.srt")
    ass = cap.write_ass(cues, d / "captions.ass", (w, h), "bottom")

    inputs = ["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(narration)]
    graph = [f"[0:v]setsar=1,ass={ass_path(ass)},fade=t=in:st=0:d=0.6,"
             f"fade=t=out:st={max(0, total - 1):.2f}:d=1[vf]"]
    mpath = None
    if music:
        if _is_media(music, AUDIO_EXT):
            mpath = Path(music).expanduser()
        else:
            job = sd.ensure_song(music)
            mpath = sd.render_mix(job, d / "music-bed.wav",
                                  {n: (0.0 if n == "vocals" else 1.0) for n in sd.stem_names(job)})
    if mpath:
        inputs += ["-stream_loop", "-1", "-i", str(mpath)]
        graph += ["[1:a]aformat=channel_layouts=stereo,asplit[nv][sc]",
                  f"[2:a]aformat=channel_layouts=stereo,volume={music_gain}[mb]",
                  "[mb][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450[duck]",
                  f"[nv][duck]amix=inputs=2:normalize=0:duration=first,"
                  f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5,loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
    else:
        graph += ["[1:a]aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]"]
    dest = render(inputs, ";".join(graph), "vf", d / f"{safe_name(title)}.mp4", "a",
                  extra=["-t", f"{total:.3f}"])
    thumb = gfx.thumbnail(gfx.grab_frame(dest, intro + 1, d / "frame.jpg"), title, (w, h),
                          d / "thumbnail.png")
    return "\n".join([
        f"Narrated video ({len(paras)} slides, {total:.0f} s): {dest}",
        f"Subtitles (from the script, timed to the voice): {srt}", f"Thumbnail: {thumb}",
        f"Visuals: {'your ' + str(len(pics)) + ' photos/clips' if pics else 'generated text slides'}"
        + (f"; music bed: {mpath.name}" if mpath else "")])


def captions(video: str, style: str = "bottom", burn: bool = True, language: str | None = None) -> str:
    """Whisper subtitles for any video: always an .srt next to the output; burn=True also
    renders a copy with the captions in the picture (style bottom or pop)."""
    src = Path(video).expanduser()
    info = probe(src)
    if not info["has_audio"]:
        raise ValueError("That video has no audio to transcribe")
    d = vout("captions", src.stem)
    segs = cap.transcribe(src, language)
    if not segs:
        return "No speech found."
    cues = cap.cues_from_whisper(segs) if style != "pop" else cap.pop_cues(segs)
    srt = cap.write_srt(cues, d / f"{safe_name(src.stem)}.srt")
    report = [f"Subtitles ({len(cues)} cues): {srt}"]
    if burn and info["has_video"]:
        w, h = info["width"] - info["width"] % 2, info["height"] - info["height"] % 2
        ass = cap.write_ass(cues, d / "captions.ass", (w, h), style)
        dest = render(["-i", str(src)], f"[0:v]scale={w}:{h},setsar=1,ass={ass_path(ass)}[v]", "v",
                      d / f"{safe_name(src.stem)}-captioned.mp4", "0:a")
        report.append(f"Captioned video: {dest}")
    return "\n".join(report)
