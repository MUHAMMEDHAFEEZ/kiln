#!/usr/bin/env bash
# Refreshes Kiln's data caches in the background and returns immediately.
#   update-data.sh projects | github | accounts
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

enabled() {   # enabled github  (GitHub is on unless switched off in config.json)
  [ "$(cfg "c.get('integrations', {}).get('$1', True)")" = True ]
}

projects() {
  # the walk and the git questions live in scan-projects.py (it reads project_dirs / project_exclude itself)
  /usr/bin/python3 "$(dirname "$0")/scan-projects.py"
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

accounts() {
  # Tasks and meetings of every connected service (Todoist, Trello, Jira, Slack, calendars, Lark ...).
  /usr/bin/python3 "$(dirname "$0")/accounts.py" sync
}

case "$which" in
  projects|github|accounts)
    ( flock -n 9 || exit 0; renice -n 15 $BASHPID >/dev/null 2>&1; "$which" ) 9>"$CACHE/.lock.$which" >/dev/null 2>&1 &
    ;;
esac
exit 0
