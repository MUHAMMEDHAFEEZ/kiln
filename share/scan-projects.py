#!/usr/bin/python3
"""Finds the git projects under the folders in config.json ("project_dirs", "project_exclude").

Writes ~/.cache/kiln/projects.all (every project, newest first), projects.txt (the 40 newest, what the
Projects card reads) and projects.status (what the card shows while it works). Results appear as they come
in, so the card fills up instead of waiting for the whole scan. Light on purpose: it walks the folders
itself (skipping node_modules, build folders and hidden folders), asks git about a few repositories at a
time, and is meant to be started with nice.

    scan-projects.py            one scan
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HOME = os.path.expanduser("~")
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
MAX_DEPTH = 3                      # a project may sit 3 folders below a scanned folder (a/b/c/.git)
SKIP = {"node_modules", "vendor", "venv", ".venv", "env", "build", "dist", "target", "__pycache__", "Pods",
        ".gradle", ".dart_tool", ".cache", ".git", "site-packages"}
LANGS = (("pubspec.yaml", "Dart"), ("tsconfig.json", "TypeScript"), ("manage.py", "Python"),
         ("pyproject.toml", "Python"), ("requirements.txt", "Python"), ("setup.py", "Python"),
         ("package.json", "JavaScript"), ("go.mod", "Go"), ("Cargo.toml", "Rust"), ("build.gradle", "Java"),
         ("build.gradle.kts", "Java"), ("pom.xml", "Java"))


def config():
    try:
        with open(f"{CONF}/config.json", encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def write(name, text):
    os.makedirs(CACHE, exist_ok=True)
    tmp = f"{CACHE}/{name}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, f"{CACHE}/{name}")


def status(state, found, total=None, roots=()):
    write("projects.status", json.dumps({"state": state, "found": found, "total": total, "at": time.time(),
                                         "dirs": list(roots)}))


def find_repos(roots, excluded):
    """Folders that contain a .git, newest first. Does not descend into a repository."""
    repos = []
    for root in roots:
        stack = [(root, 0)]
        while stack:
            path, depth = stack.pop()
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:
                continue
            if any(e.name == ".git" for e in entries):
                repos.append(path)
                continue
            if depth >= MAX_DEPTH:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False) and e.name not in SKIP and e.name not in excluded \
                            and not e.name.startswith("."):
                        stack.append((e.path, depth + 1))
                except OSError:
                    pass
    def fresh(p):
        try:
            return os.stat(os.path.join(p, ".git")).st_mtime
        except OSError:
            return 0
    return sorted(repos, key=fresh, reverse=True)


def git(path, *args, timeout=5):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def describe(path):
    """(timestamp, name, branch, changed files, path, language) or None for a repository without commits."""
    ts = git(path, "log", "-1", "--format=%ct")
    if not ts.isdigit():
        return None
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    dirty = len(git(path, "status", "--porcelain", "-uno", timeout=8).splitlines())
    lang = next((name for marker, name in LANGS if os.path.exists(os.path.join(path, marker))), "")
    return (int(ts), os.path.basename(path), branch, dirty, path, lang)


def publish(rows, final):
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r[0], reverse=True):
        if r[1] not in seen:                   # one entry per folder name
            seen.add(r[1])
            out.append(r)
    lines = ["|".join(str(x) for x in r) for r in out]
    write("projects.all", "\n".join(lines) + ("\n" if lines else ""))
    write("projects.txt", "\n".join([f"total|{len(out)}"] + lines[:40]) + "\n")
    return len(out)


def main():
    cfg = config()
    roots = [os.path.expanduser(r) for r in (cfg.get("project_dirs") or ["~/Desktop"])]
    roots = [r for r in roots if os.path.isdir(r)]
    excluded = set(cfg.get("project_exclude") or [])
    status("scanning", 0, roots=roots)
    repos = find_repos(roots, excluded)
    status("scanning", 0, len(repos), roots)
    rows, done = [], 0
    workers = max(1, min(4, os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(describe, p) for p in repos]
        for fut in as_completed(futures):
            done += 1
            row = fut.result()
            if row:
                rows.append(row)
            if done % 6 == 0 and done < len(repos):          # let the card fill up while we work
                status("scanning", publish(rows, False), len(repos), roots)
    found = publish(rows, True)
    status("done", found, len(repos), roots)


if __name__ == "__main__":
    sys.exit(main())
