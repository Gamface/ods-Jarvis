# jarvis-vault-sync

Gives Jarvis (Hermes) the user's **Agentic Memory vault** on the NAS
(`smb://mycloudex2ultra.local/Public/Agentic Memory/Vault`).

A user systemd service keeps `~/ods/data/hermes/vault` and the NAS folder in two-way sync every
30 seconds. Hermes already bind-mounts `~/ods/data/hermes` as `/opt/data`, so Jarvis reads and
writes the vault at `/opt/data/vault` with its normal file tools (inside its write-safe root).

Why a sync and not a mount: ODS's compose resolver rejects a CIFS volume in
`docker-compose.override.yml` (and the `hermes` service name), so an override mount would vanish
on the next ODS recreate; a host FUSE mount isn't visible to Docker without `allow_other` (sudo).

## Rules
- Changed on one side → copied to the other. Changed on both → the NAS copy wins and Jarvis's
  copy is kept beside it as `<name>.conflict-<time>.md` on both sides.
- Deleted on one side (and unchanged on the other) → moved to `.vault-trash/<time>/` on the other
  side. Nothing is erased. Folders are created, never removed.
- Skips files over 50 MB, temp/lock files and `.obsidian/workspace*`.
- State (last synced size + mtime per file): `~/ods/data/jarvis-vault-sync/state.json`.

## Settings (environment)
`VAULT_SMB_HOST` (default `169.254.92.35`, the NAS's self-assigned address on the direct Ethernet
link; `.local` names don't resolve for the library), `VAULT_SMB_PATH`, `VAULT_SMB_USER` /
`VAULT_SMB_PASSWORD` (guest by default), `VAULT_LOCAL`, `VAULT_INTERVAL`.

Guest sessions can't sign, so signing and encryption are off (the link is the direct cable).

## Install
Uses the jarvis-studio venv (`smbprotocol` installed there).
```
ln -sf "$PWD/jarvis-vault-sync.service" ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now jarvis-vault-sync
journalctl --user -u jarvis-vault-sync -f
```
One pass by hand: `~/ods/data/jarvis-studio/venv/bin/python vault_sync.py --once`.
