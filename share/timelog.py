#!/usr/bin/python3
"""Time spent on tasks. Starting a task begins a timer; finishing it, or starting another, ends it.

    timelog.py [today|week]      a report: per task and per project

The running task is kept in ~/.cache/kiln/active-task.json, the finished sessions in ~/.config/kiln/time-log.json (your
own data; it is never sent anywhere). A session shorter than 30 seconds is dropped, and one that was left running is cut
at 12 hours.
"""
import datetime
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
ACTIVE_FILE = f"{CACHE}/active-task.json"
LOG_FILE = f"{CONF}/time-log.json"
MIN_SESSION, MAX_SESSION, KEEP_DAYS = 30, 12 * 3600, 120


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def active():
    d = _read(ACTIVE_FILE, {})
    return d if d.get("id") else None


def elapsed(act, now=None):
    return max(0.0, (now or time.time()) - act["started"])


def start(task, repo=None, branch=None, now=None):
    """Begin the timer for `task` (a dict with id, title and url). Whatever ran before is ended first."""
    now = now or time.time()
    cur = active()
    if cur and cur["id"] == task["id"]:
        set_repo(task["id"], repo, branch)
        return active()
    if cur:
        stop(now)
    act = dict(id=task["id"], title=task.get("title", ""), url=task.get("url", ""), repo=repo, branch=branch, started=now)
    _write(ACTIVE_FILE, act)
    return act


def set_repo(task_id, repo, branch=None):
    """Remember where the running task is being worked on (known a moment after Start)."""
    cur = active()
    if cur and cur["id"] == task_id and (repo or branch):
        cur.update(repo=repo or cur.get("repo"), branch=branch or cur.get("branch"))
        _write(ACTIVE_FILE, cur)


def stop(now=None, task_id=None):
    """End the running timer (only if it is `task_id`, when given). Returns the finished session, if one was kept."""
    cur = active()
    if not cur or (task_id and cur["id"] != task_id):
        return None
    now = now or time.time()
    try:
        os.remove(ACTIVE_FILE)
    except OSError:
        pass
    end = min(now, cur["started"] + MAX_SESSION)
    if end - cur["started"] < MIN_SESSION:
        return None
    session = dict(id=cur["id"], title=cur.get("title", ""), repo=cur.get("repo"), start=cur["started"], end=end)
    log = _read(LOG_FILE, {})
    keep = [s for s in log.get("sessions", []) if now - s.get("end", 0) < KEEP_DAYS * 86400]
    log["sessions"] = keep + [session]
    _write(LOG_FILE, log)
    return session


def sessions(now=None):
    """Finished sessions, plus the running one counted up to now."""
    now = now or time.time()
    out = list(_read(LOG_FILE, {}).get("sessions", []))
    cur = active()
    if cur:
        out.append(dict(id=cur["id"], title=cur.get("title", ""), repo=cur.get("repo"), start=cur["started"],
                        end=min(now, cur["started"] + MAX_SESSION), live=True))
    return out


def totals(now=None):
    """{task id: seconds spent, all time}"""
    out = {}
    for s in sessions(now):
        out[s["id"]] = out.get(s["id"], 0) + max(0, s["end"] - s["start"])
    return out


def on_day(day, now=None):
    """{task id: seconds spent on `day` (a date)}"""
    lo = datetime.datetime.combine(day, datetime.time()).timestamp()
    hi = lo + 86400
    out = {}
    for s in sessions(now):
        secs = min(s["end"], hi) - max(s["start"], lo)
        if secs > 0:
            out[s["id"]] = out.get(s["id"], 0) + secs
    return out


def by_repo(since, now=None):
    """{folder: seconds} for sessions after `since` (a timestamp)"""
    out = {}
    for s in sessions(now):
        secs = s["end"] - max(s["start"], since)
        if secs > 0:
            key = s.get("repo") or ""
            out[key] = out.get(key, 0) + secs
    return out


def fmt(seconds):
    """'<1m', '42m', '1h 05m'"""
    m = int(seconds // 60)
    if m < 1:
        return "<1m"
    return f"{m}m" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def sync_with_board(tasks):
    """The running task was completed (or reopened as not started) somewhere else: end its timer."""
    cur = active()
    if not cur:
        return
    import accounts as A
    for t in tasks or []:
        if t.get("id") == cur["id"] and A.LarkBase.kind(t.get("status")) != "doing":
            stop(task_id=cur["id"])
            return


def report(period="today", now=None):
    now = now or time.time()
    today = datetime.date.fromtimestamp(now)
    days = [today] if period == "today" else [today - datetime.timedelta(days=i) for i in range(6, -1, -1)]
    titles = {s["id"]: s.get("title", "") for s in sessions(now)}
    per_task, per_repo, lines = {}, {}, []
    for d in days:
        day_tasks = on_day(d, now)
        for tid, secs in day_tasks.items():
            per_task[tid] = per_task.get(tid, 0) + secs
        if period != "today":
            lines.append(f"{d.strftime('%a %-d %b')}  {fmt(sum(day_tasks.values())) if day_tasks else '—'}")
    lo = datetime.datetime.combine(days[0], datetime.time()).timestamp()
    for repo, secs in by_repo(lo, now).items():
        name = os.path.basename(repo.rstrip("/")) or "(no project)"
        per_repo[name] = per_repo.get(name, 0) + secs
    out = [f"Time · {'today' if period == 'today' else 'last 7 days'}  ({fmt(sum(per_task.values())) if per_task else 'nothing yet'})"]
    if lines:
        out += [""] + lines
    if per_repo:
        out += ["", "By project"] + [f"  {fmt(s):>7}  {name}" for name, s in sorted(per_repo.items(), key=lambda kv: -kv[1])]
    if per_task:
        out += ["", "By task"] + [f"  {fmt(s):>7}  {titles.get(t, t)}" for t, s in sorted(per_task.items(), key=lambda kv: -kv[1])]
    return "\n".join(out) + "\n"


def main(argv):
    sys.stdout.write(report(argv[0] if argv and argv[0] in ("today", "week") else "today"))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
