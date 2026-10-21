#!/usr/bin/python3
"""Add or remove Kiln's SessionStart hook in Claude Code's user settings.

    claude-hook.py add <path/to/claude-hook.sh>
    claude-hook.py remove

The settings file (~/.claude/settings.json) is backed up next to itself before any change,
and everything else in it is left as it is.
"""
import json
import os
import shutil
import sys
import time

SETTINGS = os.path.expanduser("~/.claude/settings.json")
MARK = "claude-hook.sh"


def load():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, f"{SETTINGS}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, SETTINGS)


def strip(data):
    groups = data.get("hooks", {}).get("SessionStart", [])
    kept = []
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if MARK not in h.get("command", "") or "kiln" not in h.get("command", "")]
        if g["hooks"]:
            kept.append(g)
    if "hooks" in data:
        if kept:
            data["hooks"]["SessionStart"] = kept
        else:
            data["hooks"].pop("SessionStart", None)
        if not data["hooks"]:
            data.pop("hooks")
    return data


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("add", "remove"):
        sys.exit(__doc__)
    data = strip(load())
    if sys.argv[1] == "add":
        script = os.path.abspath(sys.argv[2])
        data.setdefault("hooks", {}).setdefault("SessionStart", []).append(
            {"hooks": [{"type": "command", "command": f"{script} 2>/dev/null || true", "timeout": 10}]})
        print(f"  Claude Code will see your project todos (hook in {SETTINGS})")
    else:
        print("  removed Kiln's Claude Code hook")
    save(data)


if __name__ == "__main__":
    main()
