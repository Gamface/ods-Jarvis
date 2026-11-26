"""Two-way sync between the Agentic Memory vault on the NAS and a local folder Jarvis can use.

NAS:   \\\\<host>\\Public\\Agentic Memory\\Vault   (SMB, guest)
Local: ~/ods/data/hermes/vault                   (Hermes sees it as /opt/data/vault)

Every INTERVAL seconds both sides are listed and compared with the state saved after the last
sync, so we know which side changed:
  - changed on one side only        -> copied to the other side
  - changed on both sides           -> the NAS copy wins; the local copy is kept next to it as
                                       "<name>.conflict-<time>.<ext>" (on both sides)
  - deleted on one side, unchanged  -> moved into .vault-trash/ on the other side (never erased)
  - new on one side                 -> copied
Folders are created on both sides but never removed. Nothing here deletes data outright.
"""

import json
import logging
import os
import posixpath
import signal
import sys
import time
from pathlib import Path

import smbclient
from smbprotocol.exceptions import SMBException

HOST = os.environ.get("VAULT_SMB_HOST", "169.254.92.35")
SHARE_PATH = os.environ.get("VAULT_SMB_PATH", r"Public\Agentic Memory\Vault")
LOCAL = Path(os.environ.get("VAULT_LOCAL", Path.home() / "ods/data/hermes/vault"))
STATE = Path(os.environ.get("VAULT_STATE", Path.home() / "ods/data/jarvis-vault-sync/state.json"))
INTERVAL = int(os.environ.get("VAULT_INTERVAL", "30"))
MAX_BYTES = 50 * 1024 * 1024
TRASH = ".vault-trash"
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".sync-part"}
SKIP_SUFFIXES = (".part", ".sync-part", ".tmp", ".swp", "~")

log = logging.getLogger("vault-sync")
REMOTE = "\\\\" + HOST + "\\" + SHARE_PATH.strip("\\")
SMB_KW = {"require_signing": False, "encrypt": False}


def skip(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (parts[0] == TRASH or name in SKIP_NAMES or name.startswith("~$") or name.startswith(".~")
            or name.endswith(SKIP_SUFFIXES) or (parts[0] == ".obsidian" and name.startswith("workspace")))


def rpath(rel: str) -> str:
    return REMOTE + ("\\" + rel.replace("/", "\\") if rel else "")


def sig(mtime: float, size: int) -> list:
    return [int(round(mtime)), int(size)]


def list_local() -> tuple[dict, set]:
    files, dirs = {}, set()
    for root, dnames, fnames in os.walk(LOCAL):
        rel_root = os.path.relpath(root, LOCAL).replace(os.sep, "/")
        rel_root = "" if rel_root == "." else rel_root
        dnames[:] = [d for d in dnames if not skip(posixpath.join(rel_root, d) if rel_root else d)]
        for d in dnames:
            dirs.add(posixpath.join(rel_root, d) if rel_root else d)
        for f in fnames:
            rel = posixpath.join(rel_root, f) if rel_root else f
            if skip(rel):
                continue
            st = os.stat(os.path.join(root, f))
            if st.st_size <= MAX_BYTES:
                files[rel] = sig(st.st_mtime, st.st_size)
    return files, dirs


def list_remote() -> tuple[dict, set]:
    files, dirs = {}, set()
    todo = [""]
    while todo:
        rel_root = todo.pop()
        for e in smbclient.scandir(rpath(rel_root), **SMB_KW):
            rel = posixpath.join(rel_root, e.name) if rel_root else e.name
            if skip(rel):
                continue
            if e.is_dir():
                dirs.add(rel)
                todo.append(rel)
            else:
                st = e.stat()
                if st.st_size <= MAX_BYTES:
                    files[rel] = sig(st.st_mtime, st.st_size)
    return files, dirs


def ensure_remote_dir(rel: str):
    if rel:
        smbclient.makedirs(rpath(rel), exist_ok=True, **SMB_KW)


def download(rel: str, mtime: float | None = None, dest_rel: str | None = None) -> list:
    dest = LOCAL / (dest_rel or rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with smbclient.open_file(rpath(rel), mode="rb", **SMB_KW) as src, open(tmp, "wb") as out:
        while chunk := src.read(1 << 20):
            out.write(chunk)
    if mtime is None:
        mtime = smbclient.stat(rpath(rel), **SMB_KW).st_mtime
    os.utime(tmp, (mtime, mtime))
    os.replace(tmp, dest)
    st = dest.stat()
    return sig(st.st_mtime, st.st_size)


def upload(rel: str, dest_rel: str | None = None) -> list:
    src = LOCAL / rel
    dest_rel = dest_rel or rel
    ensure_remote_dir(posixpath.dirname(dest_rel))
    tmp = rpath(dest_rel) + ".sync-part"
    with open(src, "rb") as f, smbclient.open_file(tmp, mode="wb", **SMB_KW) as out:
        while chunk := f.read(1 << 20):
            out.write(chunk)
    m = src.stat().st_mtime
    smbclient.utime(tmp, times=(m, m), **SMB_KW)
    smbclient.replace(tmp, rpath(dest_rel), **SMB_KW)
    st = smbclient.stat(rpath(dest_rel), **SMB_KW)
    return sig(st.st_mtime, st.st_size)


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def trash_local(rel: str):
    dest = LOCAL / TRASH / stamp() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(LOCAL / rel, dest)
    log.info("NAS deleted %s -> moved local copy to %s", rel, dest.relative_to(LOCAL))


def trash_remote(rel: str):
    dest_rel = f"{TRASH}/{stamp()}/{rel}"
    ensure_remote_dir(posixpath.dirname(dest_rel))
    smbclient.rename(rpath(rel), rpath(dest_rel), **SMB_KW)
    log.info("Jarvis deleted %s -> moved NAS copy to %s", rel, dest_rel)


def conflict_name(rel: str) -> str:
    base, ext = posixpath.splitext(rel)
    return f"{base}.conflict-{stamp()}{ext}"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=0, sort_keys=True))
    os.replace(tmp, STATE)


def sync_once(state: dict) -> dict:
    LOCAL.mkdir(parents=True, exist_ok=True)
    lf, ld = list_local()
    rf, rd = list_remote()
    for d in sorted(rd - ld):
        (LOCAL / d).mkdir(parents=True, exist_ok=True)
    for d in sorted(ld - rd):
        ensure_remote_dir(d)
    new = {}
    for rel in sorted(set(lf) | set(rf) | set(state)):
        L, R, S = lf.get(rel), rf.get(rel), state.get(rel)
        if L and R:
            if L == R or (S and L == S["l"] and R == S["r"]):
                new[rel] = {"l": L, "r": R}
                continue
            l_changed = not S or L != S["l"]
            r_changed = not S or R != S["r"]
            if S and l_changed and not r_changed:
                new[rel] = {"l": L, "r": upload(rel)}
                log.info("up   %s", rel)
            elif S and r_changed and not l_changed:
                new[rel] = {"l": download(rel), "r": R}
                log.info("down %s", rel)
            elif not S and L[1] == R[1] and abs(L[0] - R[0]) <= 2:
                new[rel] = {"l": L, "r": R}
            else:  # both changed (or first sight and different): NAS wins, keep Jarvis's copy beside it
                c = conflict_name(rel)
                os.replace(LOCAL / rel, LOCAL / c)
                cr = upload(c)
                new[c] = {"l": list_one_local(c), "r": cr}
                new[rel] = {"l": download(rel), "r": R}
                log.warning("conflict %s: NAS version kept, Jarvis's saved as %s", rel, c)
        elif L and not R:
            if S and L == S["l"]:
                trash_local(rel)
            else:
                new[rel] = {"l": L, "r": upload(rel)}
                log.info("up   %s (new)", rel)
        elif R and not L:
            if S and R == S["r"]:
                trash_remote(rel)
            else:
                new[rel] = {"l": download(rel), "r": R}
                log.info("down %s (new)", rel)
    return new


def list_one_local(rel: str) -> list:
    st = (LOCAL / rel).stat()
    return sig(st.st_mtime, st.st_size)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    for name in ("smbprotocol", "smbclient", "spnego"):
        logging.getLogger(name).setLevel(logging.WARNING)
    smbclient.ClientConfig(username=os.environ.get("VAULT_SMB_USER", "guest"),
                           password=os.environ.get("VAULT_SMB_PASSWORD", ""),
                           auth_protocol="ntlm", require_secure_negotiate=False)
    once = "--once" in sys.argv
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    state = load_state()
    log.info("syncing %s <-> %s every %ss", REMOTE, LOCAL, INTERVAL)
    down_since = None
    while not stop["now"]:
        try:
            state = sync_once(state)
            save_state(state)
            if down_since:
                log.info("NAS reachable again")
                down_since = None
        except (SMBException, OSError, ValueError) as e:
            if not down_since:
                log.warning("sync failed (%s); will keep retrying", e)
                down_since = time.time()
            try:
                smbclient.reset_connection_cache()
            except Exception:
                pass
        if once:
            break
        for _ in range(INTERVAL):
            if stop["now"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
