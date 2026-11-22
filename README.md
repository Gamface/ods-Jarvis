# ods-Jarvis

Jarvis is a set of extensions for [ODS](https://github.com/Osmantic/ODS) that let the ODS Hermes agent control this computer. It works locally and from your phone over Tailscale, and asks you before it does anything risky.

## Contents

- `jarvis-host-setup.sh` sets up the host once. It installs `python3-venv` and `ydotool` and creates a `ydotoold` service whose socket only your user can use. Run it with `sudo bash jarvis-host-setup.sh`.
- `extensions/services/jarvis-hostctl/` is an MCP server that gives Hermes these tools:
  - `run_command`
  - `screenshot`
  - `request_control`
  - `mouse_click` / `mouse_move`
  - `scroll`
  - `type_text`
  - `press_keys`
  - `ask_approval`

- `extensions/services/jarvis-studio/` is a music studio for Hermes. It connects StemDeck, Audacity, Ardour, Guitarix, OBS, ACE-Step, MOSS-TTS-Nano and basic-pitch with MuseScore through five music workflows (practice, DAW sessions, karaoke, AI re-production, sheet music) and five video workflows (music videos, performance videos, beat-cut edits, shorts, narrated videos) plus captions. It has a local dashboard at http://127.0.0.1:7722. See its [README](extensions/services/jarvis-studio/README.md).

## Safety model

- Shell commands outside a read-only allowlist need your approval before they run. The prompt appears as a popup on the computer or a notification on your phone.
- Mouse and keyboard tools only work during a time-limited control grant that you approve for a stated task.
- Every action goes into an audit log.
- Requests need the bearer token in `JARVIS_HOSTCTL_TOKEN`. Never commit that token.
