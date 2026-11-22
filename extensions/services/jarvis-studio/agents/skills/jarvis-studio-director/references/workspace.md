# Workspace file formats (`~/Studio`)

## `pipeline/<stage>/<slug>/brief.yaml`

The `status` field is the source of truth. The folder stage should match it.

```yaml
slug: neon-street-lofi-short
title: "Neon Street, but lo-fi"
created: 2026-11-22
status: idea          # idea | scripted | approved_script | producing | in_review | approved_post | published | measured | rejected
format: music_video   # music_video | performance | auto_edit | narrated | shorts
song: neon-street-test-song     # StemDeck title/id, if any
evidence: "3 outliers in niche at 4-9x channel average; 12 comments asking for lo-fi versions"
hook: "This song, but at 2am"
platforms: [shorts, reels, tiktok]
style:
  aspect: "9:16"
  look: spectrum      # stems | spectrum | waves
  pace: medium
  music: song         # song | new:<ACE-Step prompt> | none
  voice: brand/voice.wav
renders: []
review: null          # path to review.md once in 4-review
links: {}             # platform -> url once published
history:
  - {at: 2026-11-22T09:00, by: format-picker, note: "created from research/2026-W47"}
```

## `pipeline/4-review/<slug>/review.md`

```markdown
# Review: Neon Street, but lo-fi

**Video:** renders/neon-street-test-song-spectrum-9x16.mp4 (30 s, 9:16)
**Thumbnail:** renders/thumbnail-spectrum-9x16.png
**Platforms and time:** Shorts, Reels, TikTok · Tue 19:00
**Caption:** This song, but at 2am 🌙 … #lofi #synthwave #producer

| QA check | Result |
|---|---|
| Captions | ✅ |
| Loudness | ✅ -14.2 LUFS |
| Aspect | ✅ 9:16 |
| Hook in the first 3 s | ✅ |
| On-brand | ✅ |

Reply **approve**, **change: …** or **reject**.
```

## `reports/morning/<date>.md`

Keep it to one screen.

```markdown
# Morning page: Sat 22 Nov

**Waiting on you (3):** 2 scripts (gate 1), 1 video (gate 2), 1 buyer reply
**Yesterday:** 2 posted · 4,310 views · 38 comments (31 replies drafted) · 1 new lead
**Booked:** Leo Park, call Tue 14:00, from video "Neon Street lo-fi"
**Today:** 19:00 post "Neon Street, but lo-fi" (Shorts, Reels, TikTok)
**Working best:** 2am lo-fi edits, 5.1x average · **Weakest:** narrated explainers, 0.6x
**Problems:** none

$ jarvis-studio status --today
scripts written  3   videos made   2   posted      2
replies drafted 31   buyers        1   waiting on you  3
```

## `clients/clients.db` (SQLite)

```sql
CREATE TABLE clients (
  id INTEGER PRIMARY KEY,
  name TEXT, handle TEXT, platform TEXT,
  source_video TEXT,          -- slug of the video that brought them
  first_message TEXT,
  stage TEXT DEFAULT 'lead',  -- lead | replied | booked | client | lost
  value REAL, next_step TEXT, next_date TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT
);
CREATE TABLE touches (
  id INTEGER PRIMARY KEY, client_id INTEGER REFERENCES clients(id),
  at TEXT DEFAULT CURRENT_TIMESTAMP, kind TEXT,   -- comment | dm | email | call
  direction TEXT, text TEXT, approved_by_user INTEGER DEFAULT 0
);
```

## `brand/brand.md`

```markdown
# Brand
Niche: … · Audience: … · Promise: what viewers get from every video
Voice: 3 adjectives · Words we use / never use
Look: colours (hex), fonts, intro/outro, logo position
Do: … · Don't: …
Offer and booking link: …
```
