#!/usr/bin/env bash
# Refreshes Kiln's data caches in the background and returns immediately.
#   update-data.sh projects | github | lark
# Which folders are scanned for git projects, and which integrations run, is set in
# ~/.config/kiln/config.json ("project_dirs", "project_exclude", "integrations").
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
CONF="${KILN_CONFIG:-$HOME/.config/kiln}"
CACHE="${KILN_CACHE:-$HOME/.cache/kiln}"
mkdir -p "$CACHE"
which="${1:-}"

# cfg <python expression on `c` (the parsed config, {} if missing)>
cfg() {
  /usr/bin/python3 - "$CONF/config.json" "$1" <<'PY' 2>/dev/null
import json, sys
try:
    c = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    c = {}
v = eval(sys.argv[2], {"c": c})
print("\n".join(map(str, v)) if isinstance(v, list) else ("" if v is None else v))
PY
}

enabled() {   # enabled github|lark  (GitHub is on unless switched off, Lark is opt-in)
  local def=True; [ "$1" = lark ] && def=False
  [ "$(cfg "c.get('integrations', {}).get('$1', $def)")" = True ]
}

projects() {
  mapfile -t roots < <(cfg "c.get('project_dirs', ['~/Desktop'])")
  [ ${#roots[@]} -gt 0 ] || roots=("~/Desktop")
  mapfile -t skip < <(cfg "c.get('project_exclude', [])")
  excl=(-not -path '*/node_modules/*' -not -path '*/.cache/*' -not -path '*/vendor/*')
  for s in "${skip[@]}"; do [ -n "$s" ] && excl+=(-not -path "*/$s/*"); done
  dirs=()
  for r in "${roots[@]}"; do r="${r/#\~/$HOME}"; [ -d "$r" ] && dirs+=("$r"); done
  [ ${#dirs[@]} -gt 0 ] || { : > "$CACHE/projects.all"; echo "total|0" > "$CACHE/projects.txt"; return; }
  {
    while IFS= read -r g; do
      r="$(dirname "$g")"
      ts="$(git -C "$r" log -1 --format=%ct 2>/dev/null)"
      [ -z "$ts" ] && continue
      br="$(git -C "$r" rev-parse --abbrev-ref HEAD 2>/dev/null)"
      d="$(timeout 8 git -C "$r" status --porcelain -uno 2>/dev/null | wc -l)"
      lang=""
      if   [ -f "$r/pubspec.yaml" ]; then lang=Dart
      elif [ -f "$r/tsconfig.json" ]; then lang=TypeScript
      elif [ -f "$r/manage.py" ] || [ -f "$r/pyproject.toml" ] || [ -f "$r/requirements.txt" ] || [ -f "$r/setup.py" ]; then lang=Python
      elif [ -f "$r/package.json" ]; then lang=JavaScript
      elif [ -f "$r/go.mod" ]; then lang=Go
      elif [ -f "$r/Cargo.toml" ]; then lang=Rust
      elif [ -f "$r/build.gradle" ] || [ -f "$r/build.gradle.kts" ] || [ -f "$r/pom.xml" ]; then lang=Java
      fi
      echo "$ts|$(basename "$r")|$br|$d|$r|$lang"
    done < <(find "${dirs[@]}" -maxdepth 4 -name .git -type d "${excl[@]}" 2>/dev/null)
  } | sort -t'|' -k1 -nr | awk -F'|' '!seen[$2]++' > "$CACHE/projects.all"
  total="$(wc -l < "$CACHE/projects.all")"
  { echo "total|$total"; head -40 "$CACHE/projects.all"; } > "$CACHE/projects.tmp" \
    && mv "$CACHE/projects.tmp" "$CACHE/projects.txt"
}

github() {
  enabled github || exit 0
  n="$(gh api notifications --jq length 2>/dev/null)"
  [[ "$n" =~ ^[0-9]+$ ]] || exit 0            # offline / not logged in: keep the previous cache
  prs="$(gh search prs --author=@me --state=open --limit 20 --json repository,number,title,updatedAt \
          --jq '.[] | "\(.repository.nameWithOwner)|\(.number)|\(.updatedAt | fromdateiso8601)|\(.title)"' 2>/dev/null)"
  rev="$(gh search prs --review-requested=@me --state=open --limit 50 --json number --jq length 2>/dev/null)"
  [[ "$rev" =~ ^[0-9]+$ ]] || rev=0
  user="$(gh api user --jq .login 2>/dev/null)"
  {
    echo "user|${user:-github}"
    echo "notif|$n"
    echo "rev|$rev"
    echo "prcount|$(printf '%s\n' "$prs" | grep -c .)"
    printf '%s\n' "$prs" | grep . | head -3 | sed 's/^/pr|/'
    echo "updated|$(date +%s)"
  } > "$CACHE/github.tmp" && mv "$CACHE/github.tmp" "$CACHE/github.txt"

  # richer data for the GitHub card: your open PRs with CI state, review requests, contributions
  q='query($q:String!){ viewer{ login
      contributionsCollection{ contributionCalendar{ totalContributions weeks{ contributionDays{ contributionCount date } } } }
      pullRequests(states:OPEN, first:10, orderBy:{field:UPDATED_AT, direction:DESC}){ totalCount
        nodes{ number title url updatedAt isDraft repository{ nameWithOwner }
               commits(last:1){ nodes{ commit{ statusCheckRollup{ state } } } } } } }
    reviews: search(query:$q, type:ISSUE, first:10){ issueCount
      nodes{ ... on PullRequest{ number title url updatedAt repository{ nameWithOwner } author{ login } } } } }'
  if gh api graphql -f query="$q" -f q="is:pr is:open archived:false review-requested:${user}" > "$CACHE/github-gql.tmp" 2>/dev/null \
     && jq -e '.data.viewer.login' "$CACHE/github-gql.tmp" >/dev/null 2>&1; then
    jq --argjson n "$n" '. + {notif: $n}' "$CACHE/github-gql.tmp" > "$CACHE/github.json.tmp" \
      && mv "$CACHE/github.json.tmp" "$CACHE/github.json"
  fi
  rm -f "$CACHE/github-gql.tmp"
}

lark() {
  enabled lark || { rm -f "$CACHE"/lark-*.json; exit 0; }
  # Lark tasks + today's agenda through the official lark-cli (credentials live in the OS keychain).
  command -v lark-cli >/dev/null 2>&1 || { echo '{"ok":false,"error":{"type":"missing"}}' > "$CACHE/lark-error.json"; exit 0; }
  out="$(lark-cli task +get-my-tasks --as user --complete=false --format json 2>&1)"
  if printf '%s' "$out" | jq -e 'type == "object" and .ok == false' >/dev/null 2>&1 || ! printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
    printf '%s' "$out" > "$CACHE/lark-error.json"
    exit 0
  fi
  printf '%s' "$out" > "$CACHE/lark-tasks.json.tmp" && mv "$CACHE/lark-tasks.json.tmp" "$CACHE/lark-tasks.json"
  rm -f "$CACHE/lark-error.json"
  ag="$(lark-cli calendar +agenda --as user --format json 2>/dev/null)"
  if printf '%s' "$ag" | jq -e . >/dev/null 2>&1 && ! printf '%s' "$ag" | jq -e 'type == "object" and .ok == false' >/dev/null 2>&1; then
    printf '%s' "$ag" > "$CACHE/lark-agenda.json.tmp" && mv "$CACHE/lark-agenda.json.tmp" "$CACHE/lark-agenda.json"
  fi
}

case "$which" in
  projects|github|lark)
    ( flock -n 9 || exit 0; "$which" ) 9>"$CACHE/.lock.$which" >/dev/null 2>&1 &
    ;;
esac
exit 0
