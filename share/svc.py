#!/usr/bin/python3
"""Programs that a Scripts widget starts and stops (a server, an app): started in their own process group, with their output in
a log file, remembered across Kiln restarts, and stopped with a command of yours or with SIGTERM (then SIGKILL).

    spawn(command, cwd, logfile, env)   start `bash -c command` detached; returns the process
    alive(pid, started)                 is that very process still there (a reused pid does not count)
    terminate(pid, grace)               SIGTERM to its whole group, SIGKILL after `grace` seconds; blocks
    run_check(command)                  does a check command ("is it running?") succeed

No GTK in here, so it can be tested without a display."""
import json
import os
import signal
import subprocess
import time

HOME = os.path.expanduser("~")
LOG_LIMIT = 200_000          # a log is cut back to its last bytes when it grows beyond this


def start_time(pid):
    """The start time (in clock ticks since boot) of a process, or None. Together with the pid it names one process."""
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as f:
            data = f.read()
        return int(data[data.rindex(")") + 2:].split()[19])          # field 22; the name (field 2) may hold spaces
    except (OSError, ValueError, IndexError):
        return None


def alive(pid, started=None):
    """Is process `pid` running (and, when `started` is given, is it the same one that started then)?"""
    t = start_time(pid)
    if t is None:
        return False
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError, ValueError):
        return False
    return state != "Z" and (started is None or t == started)


def spawn(command, cwd=None, logfile=None, env=None):
    """Start `bash -c command` in a process group of its own (so everything it starts can be stopped), reading nothing and
    writing its output to `logfile` (emptied first)."""
    cwd = os.path.expanduser(cwd) if cwd and os.path.isdir(os.path.expanduser(cwd)) else HOME
    out = subprocess.DEVNULL
    if logfile:
        os.makedirs(os.path.dirname(logfile), mode=0o700, exist_ok=True)
        open(logfile, "wb").close()                                   # emptied; the child appends (so trimming it is safe)
        out = open(logfile, "ab", buffering=0)                       # (the child keeps its own copy of the descriptor)
        os.chmod(logfile, 0o600)
    try:
        return subprocess.Popen(["/bin/bash", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True, env=env)
    finally:
        if out is not subprocess.DEVNULL:
            out.close()


def terminate(pid, grace=5.0, started=None):
    """Stop the process group of `pid`: SIGTERM, and SIGKILL when it is still there after `grace` seconds. Returns True when
    it is gone. Blocks, so run it in a thread."""
    if not alive(pid, started):
        return True
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(int(pid), sig)                                  # the group: the server and what it started
        except ProcessLookupError:
            return True
        except OSError:
            try:
                os.kill(int(pid), sig)                                # not a group leader: just the process
            except OSError:
                return not alive(pid, started)
        end = time.time() + wait
        while time.time() < end:
            if not alive(pid, started):
                return True
            time.sleep(0.1)
    return not alive(pid, started)


def run_check(command, timeout=5):
    """True when the check command ("is it running?") exits with 0."""
    if not command or not str(command).strip():
        return False
    try:
        r = subprocess.run(["/bin/bash", "-c", str(command)], cwd=HOME, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


# ---------------------------------------------------------------- remembered processes
def remember(path, pid, started, command):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": int(pid), "started": started, "command": command, "at": time.time()}, f)
    os.replace(tmp, path)


def recall(path, command=None):
    """(pid, started) of the process remembered at `path` when it is still running (and was started for this `command`),
    else None."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        pid, started = int(d["pid"]), d.get("started")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if command is not None and d.get("command") != command:
        return None
    return (pid, started) if alive(pid, started) else None


def forget(path):
    try:
        os.remove(path)
    except OSError:
        pass


def tail(path, lines=40, limit=6000):
    """The last `lines` lines (at most `limit` characters) of a log, or ''."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - limit * 2))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    text = "\n".join(data.replace("\r", "").splitlines()[-lines:])
    return text[-limit:]


def trim(path, keep=LOG_LIMIT // 2):
    """Cut a log that has grown beyond LOG_LIMIT back to its last `keep` bytes."""
    try:
        if os.path.getsize(path) <= LOG_LIMIT:
            return
        with open(path, "rb") as f:
            f.seek(-keep, os.SEEK_END)
            data = f.read()
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        pass
