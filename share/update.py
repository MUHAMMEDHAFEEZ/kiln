#!/usr/bin/env python3
"""Kiln updates: finds the newest GitHub release and installs it.

    update.py status            installed version, newest release, what the last check found
    update.py check             ask GitHub now
    update.py apply [--yes]     install the newest release (asks first unless --yes)
    update.py auto              what Kiln runs once in a while: check, then do what "updates.mode" says
    update.py rollback          go back to the version that was installed before the last update

"updates.mode" in ~/.config/kiln/config.json:  "notify" (default: a notification, you press update),
"auto" (install it quietly when you are not in Focus mode) or "off".

Nothing is installed unless the release archive carries a signature made with the project's release key
(share/release-key.pub, checked with `ssh-keygen -Y verify`). The private half of that key lives only with the
maintainer, so a stolen GitHub account or token cannot push an update to anyone. With no key, or an archive
whose signature does not match, Kiln refuses and says so.

An update downloads the signed archive, installs it exactly like ./install.sh --no-apply, keeps your settings,
themes and lists, and refreshes the app icon and accent colours. The previous version is kept in
~/.local/share/kiln.prev; if the new one does not start, it is put back by itself.
Standard library only (and `gh` when it is installed and logged in, which also works while the repository is
private).
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
SHARE = os.path.dirname(os.path.abspath(__file__))
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
STATE = f"{CACHE}/update.json"
KEY_FILE = f"{SHARE}/release-key.pub"           # one or more public keys ("ssh-ed25519 AAAA… comment"), one per line
SIG_NAMESPACE = "kiln-release"
REPO = os.environ.get("KILN_UPDATE_REPO", "MUHAMMEDHAFEEZ/kiln")
API = os.environ.get("KILN_UPDATE_API", "https://api.github.com").rstrip("/")
EXT = f"{HOME}/.local/share/gnome-shell/extensions/kiln-shell@local"
MAX_DOWNLOAD = 60 * 1024 * 1024


class UpdateError(Exception):
    """A problem worth showing to the user, in plain words."""


# ----------------------------------------------------------------- versions and state
def parse(v):
    """(1, 2, 0) from 'v1.2.0' or '1.2.0'; None for anything else (pre-releases are not offered)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def installed():
    try:
        with open(f"{SHARE}/VERSION", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**changes):
    d = load_state()
    d.update(changes)
    os.makedirs(CACHE, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, STATE)
    return d


def mode():
    try:
        with open(f"{CONF}/config.json", encoding="utf-8") as f:
            m = str((json.load(f).get("updates") or {}).get("mode", "notify")).lower()
    except (OSError, ValueError, AttributeError):
        m = "notify"
    return m if m in ("notify", "auto", "off") else "notify"


# ----------------------------------------------------------------- talking to GitHub
def _gh(*args, accept=None):
    """Output of `gh api ...` when gh is installed and logged in, else None."""
    if not shutil.which("gh") or os.environ.get("KILN_UPDATE_NO_GH"):
        return None
    extra = ["-H", f"Accept: {accept}"] if accept else []
    try:
        r = subprocess.run(["gh", "api", *extra, *args], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout else None


def _http(path, accept="application/vnd.github+json"):
    req = urllib.request.Request(API + path, headers={"User-Agent": "Kiln-updater", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            raise UpdateError("Cannot read the releases (the repository may be private: sign in with `gh auth login`).") from None
        raise UpdateError(f"GitHub answered with an error ({e.code}).") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise UpdateError("Cannot reach GitHub. Check your connection.") from None


def fetch(path):
    raw = _gh(path.lstrip("/")) if not os.environ.get("KILN_UPDATE_API") else None
    return raw if raw is not None else _http(path)


def latest_release():
    raw = fetch(f"/repos/{REPO}/releases/latest")
    try:
        d = json.loads(raw)
    except ValueError:
        raise UpdateError("GitHub sent something Kiln does not understand.") from None
    if not parse(d.get("tag_name")) or d.get("draft") or d.get("prerelease"):
        raise UpdateError("No release found.")
    return d


def check():
    """Ask GitHub, remember the answer, return the saved state."""
    try:
        rel = latest_release()
    except UpdateError as e:
        return save_state(checked=time.time(), error=str(e), current=installed())
    cur, new = installed(), rel["tag_name"].lstrip("v")
    newer = parse(new) > (parse(cur) or (0, 0, 0))
    try:
        release_assets(rel)
        problem = None
    except UpdateError as e:
        problem, newer = str(e), False                  # an unsigned release is never offered
    return save_state(checked=time.time(), error=problem, current=cur, latest=new, url=rel.get("html_url", ""),
                      notes=(rel.get("body") or "")[:1500], newer=newer,
                      unsigned=new if problem and parse(new) > (parse(cur) or (0, 0, 0)) else None)


# ----------------------------------------------------------------- download, verify, install
def release_assets(rel):
    """(archive asset, signature asset) of a release, or an UpdateError when it is not properly signed."""
    version = rel["tag_name"].lstrip("v")
    by_name = {a.get("name"): a for a in rel.get("assets") or []}
    archive, sig = by_name.get(f"kiln-{version}.tar.gz"), by_name.get(f"kiln-{version}.tar.gz.sig")
    if not archive or not sig:
        raise UpdateError(f"Release {version} is not signed, so Kiln will not install it.")
    return archive, sig


def download_asset(asset, dest):
    """Save a release asset (through the API, which also works for a private repository)."""
    api_path = f"/repos/{REPO}/releases/assets/{asset['id']}"
    raw = None if os.environ.get("KILN_UPDATE_API") else _gh(api_path.lstrip("/"), accept="application/octet-stream")
    if raw is None:
        raw = _http(api_path, accept="application/octet-stream")
    if len(raw) > MAX_DOWNLOAD:
        raise UpdateError("The download is larger than expected; not installing it.")
    with open(dest, "wb") as f:
        f.write(raw)


def trusted_keys():
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            keys = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        keys = []
    keys = [k for k in keys if k.startswith("ssh-ed25519 ") or k.startswith("sk-ssh-ed25519")]
    if not keys:
        raise UpdateError("This copy of Kiln has no release key, so it cannot check updates. "
                          "Update by hand (git pull, then ./install.sh).")
    return keys


def verify_signature(archive, sig):
    """Raise unless `sig` is a valid signature of `archive` by one of the trusted release keys."""
    keys = trusted_keys()
    if not shutil.which("ssh-keygen"):
        raise UpdateError("ssh-keygen is missing (package openssh-client), so the signature cannot be checked.")
    with tempfile.TemporaryDirectory(dir=CACHE) as d:
        allowed = f"{d}/allowed_signers"
        with open(allowed, "w", encoding="utf-8") as f:
            f.write("".join(f"kiln-release {k}\n" for k in keys))
        with open(archive, "rb") as data:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", "kiln-release", "-n", SIG_NAMESPACE,
                                "-s", sig], stdin=data, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise UpdateError("The signature of this update is not valid, so Kiln will not install it.")


def extract(archive, into, version):
    """Unpack a release archive safely and return the folder with install.sh."""
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(into, filter="data")          # refuses absolute paths, .. and links that escape
    except (tarfile.TarError, OSError, ValueError) as e:
        raise UpdateError(f"The download is not a valid archive ({type(e).__name__}).") from None
    tops = [os.path.join(into, n) for n in os.listdir(into)]
    top = tops[0] if len(tops) == 1 and os.path.isdir(tops[0]) else None
    if not top or not os.path.exists(f"{top}/install.sh") or not os.path.exists(f"{top}/share/VERSION"):
        raise UpdateError("The download does not look like Kiln; not installing it.")
    with open(f"{top}/share/VERSION", encoding="utf-8") as f:
        if f.read().strip() != version:
            raise UpdateError("The download is not the version it says it is; not installing it.")
    return top


def file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def sh(*cmd, cwd=None, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def backup():
    for src, dst in ((SHARE, SHARE + ".prev"), (EXT, EXT + ".prev")):
        if os.path.isdir(src):
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))


def restart():
    sh(f"{SHARE}/start.sh")


def healthy(since):
    """True when Kiln is running and has not crashed with a traceback since `since` (unix seconds)."""
    time.sleep(10)
    if sh("systemctl", "--user", "is-active", "kiln")[1].strip() != "active":
        return False
    log = sh("journalctl", "--user", "-u", "kiln", "--no-pager", "--since", f"@{int(since)}")[1]
    return "Traceback" not in log


def rollback():
    prev = SHARE + ".prev"
    if not os.path.isdir(prev):
        raise UpdateError("There is no earlier version to go back to.")
    for dst, src in ((SHARE, prev), (EXT, EXT + ".prev")):
        if os.path.isdir(src):
            sh("rsync", "-a", "--delete", src + "/", dst + "/")
    sh("/usr/bin/python3", f"{SHARE}/theme.py", "--refresh")
    restart()
    save_state(current=installed(), newer=None, last_result="rolled back")


def notify(title, body):
    if shutil.which("notify-send"):
        sh("notify-send", "-a", "Kiln", "-i", "kiln", title, body)


def apply(version=None, runner=sh):
    """Install the newest release (or `version`). Returns a one-line result."""
    os.makedirs(CACHE, exist_ok=True)
    lock = open(f"{CACHE}/.update.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise UpdateError("An update is already running.") from None
    version = version or latest_release()["tag_name"].lstrip("v")
    if not parse(version):
        raise UpdateError("Not a release version.")
    before = installed()
    save_state(last_result="installing", target=version)
    work = tempfile.mkdtemp(prefix="update-", dir=CACHE)
    try:
        archive, sig = f"{work}/kiln.tar.gz", f"{work}/kiln.tar.gz.sig"
        rel = latest_release()
        if rel["tag_name"].lstrip("v") != version:
            raise UpdateError(f"{version} is not the newest release any more; run the update again.")
        a_asset, s_asset = release_assets(rel)
        download_asset(a_asset, archive)
        download_asset(s_asset, sig)
        verify_signature(archive, sig)                  # nothing below runs unless the maintainer signed this
        os.makedirs(f"{work}/src")
        top = extract(archive, f"{work}/src", version)
        backup()
        ext_before = file_hash(f"{EXT}/extension.js")
        started = time.time()
        code, out = runner("bash", f"{top}/install.sh", "--no-apply", "--no-gui", cwd=top, timeout=600)
        if code != 0:
            msg = [l for l in out.splitlines() if l.strip()][-3:]
            raise UpdateError("The installer stopped: " + " ".join(msg)[:240])
        runner("/usr/bin/python3", f"{SHARE}/theme.py", "--refresh")
        restart()
        if not healthy(started):
            rollback()
            raise UpdateError(f"Version {version} did not start, so {before} was put back.")
        new_top_bar = file_hash(f"{EXT}/extension.js") != ext_before
        result = f"updated {before} → {version}"
        save_state(current=installed(), newer=False, last_result=result, updated_at=time.time(),
                   needs_relogin=new_top_bar)
        notify(f"Kiln {version} is installed",
               "Log out and back in once to load the new top bar and lock screen." if new_top_bar
               else "Everything is up to date.")
        return result
    except UpdateError as e:
        save_state(last_result="failed: " + str(e))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        lock.close()


def auto():
    m = mode()
    if m == "off":
        return "updates are off"
    st = check()
    if st.get("unsigned") and st.get("notified_unsigned") != st["unsigned"]:
        notify(f"Kiln ignored version {st['unsigned']}",
               "It is on GitHub but is not signed with the release key. If you did not publish it, check your GitHub account.")
        save_state(notified_unsigned=st["unsigned"])
    if not st.get("newer"):
        return st.get("error") or "up to date"
    latest = st["latest"]
    if m == "auto":
        try:
            with open(f"{CACHE}/mode", encoding="utf-8") as f:
                if f.read().strip() == "focus":
                    return "waiting: you are in Focus mode"
        except OSError:
            pass
        return apply(latest)
    if st.get("notified") != latest:
        notify(f"Kiln {latest} is available", "Open Kiln Settings → General, or run: kiln update")
        save_state(notified=latest)
    return f"{latest} is available"


# ----------------------------------------------------------------- command line
def main(argv):
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "status":
            st = load_state()
            print(f"installed  {installed()}")
            if st.get("latest"):
                print(f"newest     {st['latest']}" + ("  (update available)" if st.get("newer") else ""))
            if st.get("error"):
                print("last check:", st["error"])
            if st.get("last_result"):
                print("last update:", st["last_result"])
            print(f"mode       {mode()}")
        elif cmd == "check":
            st = check()
            print(st.get("error") or (f"{st['latest']} is available" if st.get("newer") else "Kiln is up to date."))
            return 1 if st.get("error") else 0
        elif cmd == "apply":
            st = check()
            if st.get("error"):
                print(st["error"], file=sys.stderr)
                return 1
            if not st.get("newer"):
                print("Kiln is up to date.")
                return 0
            print(f"Kiln {st['current']} → {st['latest']}")
            if st.get("notes"):
                print(st["notes"].strip().split("\n## ")[0][:700])
            if "--yes" not in argv and input("Install it? [Y/n] ").strip().lower() in ("n", "no"):
                return 0
            print(apply(st["latest"]))
        elif cmd == "auto":
            print(auto())
        elif cmd == "rollback":
            rollback()
            print(f"Back to {installed()}.")
        else:
            print(__doc__)
            return 2
    except UpdateError as e:
        print(e, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
