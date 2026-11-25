"""Video style presets: one name sets the picture prompt, colour grade, captions, transitions
and camera motion for a whole explainer. Any field can still be overridden per video or scene.

Picture prompts are tuned for SD-Turbo, which can't draw words, so every prompt asks for no
text; headlines and captions are drawn over the picture instead.
"""

NO_TEXT = "no text, no words, no letters, no watermark"

# grade: ffmpeg video filters applied to the finished picture (before captions are drawn)
# captions: highlight | pop | box | subtle | none
# transition: cut | dip | flash | whoosh
# motion: zoom (alternate in/out) | pan (alternate left/right) | mixed | still
STYLES = {
    "cinematic": {
        "label": "Cinematic", "music": "cinematic, emotional strings and soft piano",
        "prompt": "cinematic film still, dramatic lighting, shallow depth of field, rich colour grading, 35mm",
        "grade": "eq=contrast=1.08:saturation=0.92,colorbalance=rs=-0.04:bs=0.05:rh=0.05:bh=-0.04,vignette=PI/5",
        "captions": "subtle", "transition": "dip", "motion": "zoom"},
    "documentary": {
        "label": "Documentary", "music": "calm ambient documentary underscore",
        "prompt": "documentary photograph, natural light, realistic, candid, high detail",
        "grade": "eq=contrast=1.03:saturation=0.86,vignette=PI/6",
        "captions": "box", "transition": "dip", "motion": "pan"},
    "3d-cartoon": {
        "label": "3D cartoon", "music": "playful upbeat orchestral, pizzicato, light percussion",
        "prompt": "3D animated cartoon render, cute stylized characters, soft global illumination, vibrant colours",
        "grade": "eq=saturation=1.18:contrast=1.05",
        "captions": "highlight", "transition": "whoosh", "motion": "mixed"},
    "anime": {
        "label": "Anime", "music": "energetic j-pop instrumental, bright synths",
        "prompt": "anime illustration, cel shading, vibrant colours, detailed painted background",
        "grade": "eq=saturation=1.22:contrast=1.06:brightness=0.02",
        "captions": "highlight", "transition": "flash", "motion": "pan"},
    "storybook": {
        "label": "Watercolor storybook", "music": "gentle music box and soft acoustic guitar",
        "prompt": "watercolor storybook illustration, soft pastel colours, paper texture, whimsical, gentle",
        "grade": "eq=saturation=0.95:brightness=0.03,colorbalance=rm=0.03:bm=-0.02",
        "captions": "subtle", "transition": "dip", "motion": "zoom"},
    "comic": {
        "label": "Comic book", "music": "punchy action drums and brass hits",
        "prompt": "comic book panel, bold ink outlines, halftone shading, vivid flat colours, dynamic angle",
        "grade": "eq=contrast=1.18:saturation=1.28,unsharp=5:5:0.6",
        "captions": "pop", "transition": "flash", "motion": "zoom"},
    "clay": {
        "label": "Claymation", "music": "quirky bouncy marimba and ukulele",
        "prompt": "stop-motion claymation, cute plasticine clay figure with rounded shapes, fingerprint clay texture, miniature handmade set, soft studio lighting",
        "grade": "eq=saturation=1.1,colorbalance=rm=0.04:bm=-0.03",
        "captions": "highlight", "transition": "dip", "motion": "zoom"},
    "sketch": {
        "label": "Pencil sketch", "music": "thoughtful solo piano",
        "prompt": "pencil sketch, graphite drawing, cross-hatching, white paper, detailed linework",
        "grade": "hue=s=0.15,eq=contrast=1.1",
        "captions": "box", "transition": "dip", "motion": "pan"},
    "flat": {
        "label": "Flat illustration", "music": "light corporate electronic, clean plucks",
        "prompt": "flat vector illustration, simple geometric shapes, clean lines, soft modern colour palette",
        "grade": "eq=saturation=1.08",
        "captions": "highlight", "transition": "cut", "motion": "zoom"},
    "retro": {
        "label": "Retro / VHS", "music": "80s synthwave, analog synths, gated drums",
        "prompt": "1980s retro photo, VHS aesthetic, neon colours, film grain, analog look",
        "grade": "eq=saturation=1.3:contrast=1.1,rgbashift=rh=3:bh=-3,noise=alls=10:allf=t,vignette=PI/4",
        "captions": "box", "transition": "flash", "motion": "zoom"},
    "whiteboard": {
        "label": "Minimal whiteboard", "music": "minimal soft electronic, light and positive",
        "prompt": "simple black marker drawing on a plain white background, minimal line art, clean, centered",
        "grade": "eq=brightness=0.04:saturation=0.6",
        "captions": "box", "transition": "cut", "motion": "still"},
    "social": {
        "label": "Social bold", "music": "upbeat trap pop beat, punchy and modern",
        "prompt": "bold colourful photo, high contrast, eye-catching, vibrant, centered subject",
        "grade": "eq=saturation=1.2:contrast=1.08",
        "captions": "pop", "transition": "whoosh", "motion": "mixed"},
}

CAPTIONS = ("highlight", "pop", "box", "subtle", "none")
TRANSITIONS = ("cut", "dip", "flash", "whoosh")
MOTIONS = ("zoom-in", "zoom-out", "pan-left", "pan-right", "pan-up", "pan-down", "still")
MOTION_SETS = {"zoom": ("zoom-in", "zoom-out"), "pan": ("pan-right", "pan-left"),
               "mixed": ("zoom-in", "pan-right", "zoom-out", "pan-left"), "still": ("still",)}
DEFAULT = {"captions": "subtle", "transition": "dip", "motion": "zoom", "grade": "", "prompt": ""}


def resolve(style: str | None) -> tuple[str | None, dict]:
    """(preset name or None, settings). A name not in STYLES is a free-text picture style."""
    key = (style or "").strip().lower().replace(" ", "-").replace("/", "-")
    aliases = {"3d": "3d-cartoon", "cartoon": "3d-cartoon", "watercolor": "storybook",
               "pencil": "sketch", "vhs": "retro", "minimal": "whiteboard", "bold": "social",
               "retro-vhs": "retro", "comic-book": "comic", "claymation": "clay"}
    key = aliases.get(key, key)
    if key in STYLES:
        return key, STYLES[key]
    if style and style.strip():
        return None, dict(DEFAULT, prompt=style.strip())
    return None, dict(DEFAULT)


def picture_prompt(subject: str, style_prompt: str) -> str:
    parts = [p for p in (subject.strip().rstrip(","), style_prompt, NO_TEXT) if p]
    return ", ".join(parts)[:1000]


def scene_motion(motion_set: str, index: int, override: str | None = None) -> str:
    if override in MOTIONS:
        return override
    seq = MOTION_SETS.get(motion_set) or MOTION_SETS["zoom"]
    return seq[index % len(seq)]


def catalog() -> str:
    return "\n".join(f"{k}: {v['label']} — captions {v['captions']}, {v['transition']} transitions, "
                     f"{v['motion']} motion" for k, v in STYLES.items())
