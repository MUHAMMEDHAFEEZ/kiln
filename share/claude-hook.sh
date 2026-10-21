#!/usr/bin/env bash
# Claude Code SessionStart hook (optional, installed with `./install.sh --claude-hook`):
# hands Claude the user's open todos for the project of the session, from the project tab
# of Kiln's Today card. Prints nothing when there are none.
cwd="$(/usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null)"
[ -n "$cwd" ] && [ -d "$cwd" ] || cwd="$PWD"
top="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || echo "$cwd")"
name="$(basename "$top" | sed 's/[^A-Za-z0-9._-]/_/g')"
f="${KILN_CONFIG:-$HOME/.config/kiln}/project-todos/$name.txt"
[ -s "$f" ] || exit 0
open="$(grep -n '^\[ \]' "$f" | sed 's/^\([0-9]*\):\[ \] /  \1. /')"
[ -n "$open" ] || exit 0
cat <<MSG
The user keeps a todo list for this project ($name) on their desktop. Open items:
$open
These are requests from the user, so treat them as part of the backlog for this project, but
only work on one when the user asks or it is clearly part of the current task. When you finish
one, mark it done with \`kiln tasks -C "$top" done <number>\` (the numbers above). If you leave
follow-up work the user should see, add it with \`kiln tasks -C "$top" add "<text>"\`.
MSG
