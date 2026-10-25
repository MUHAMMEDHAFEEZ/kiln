#!/usr/bin/env python3
"""Kiln accounts: the task and calendar services that feed the Today and Agenda cards.

    accounts.py list                     which services are connected
    accounts.py connect <service>        ask for the details and test them (interactive)
    accounts.py disconnect <service>
    accounts.py test <service>           check the saved details
    accounts.py sync [service]           refresh ~/.cache/kiln/accounts.json (what the cards read)
    accounts.py complete <service> <id>  mark one task done

Every service is one small class below: it says what it needs from the user (`fields`), checks a
login (`check`), returns tasks and events (`fetch`) and, when the service allows it, completes a
task (`complete`). Details are saved in ~/.config/kiln/accounts.json (readable by you only).
Standard library only; recurring calendar events use python3-dateutil when it is installed.
"""
import base64
import datetime
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
ACCOUNTS_FILE = f"{CONF}/accounts.json"
CACHE_FILE = f"{CACHE}/accounts.json"
TIMEOUT = 15
WEEK = 7 * 86400


class ServiceError(Exception):
    """A problem worth showing to the user, in plain words (never contains a token)."""


class Field:
    def __init__(self, key, label, secret=False, hint="", multiline=False, optional=False):
        self.key, self.label, self.secret = key, label, secret
        self.hint, self.multiline, self.optional = hint, multiline, optional


# ----------------------------------------------------------------- saved details
def load_accounts():
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_accounts(data):
    os.makedirs(CONF, mode=0o700, exist_ok=True)
    tmp = ACCOUNTS_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, ACCOUNTS_FILE)
    os.chmod(ACCOUNTS_FILE, 0o600)


def connected():
    """Ids of the services the user has connected (Lark counts when its switch in config.json is on)."""
    ids = [k for k, v in load_accounts().items() if k in SERVICES and isinstance(v, dict)]
    try:
        with open(f"{CONF}/config.json", encoding="utf-8") as f:
            if (json.load(f).get("integrations") or {}).get("lark") and "lark" not in ids:
                ids.append("lark")
    except (OSError, ValueError):
        pass
    return ids


def details(service):
    d = dict(load_accounts().get(service) or {})
    return d


# ----------------------------------------------------------------- small helpers
def http(method, url, headers=None, params=None, body=None, form=None, timeout=TIMEOUT, auth=None):
    """One JSON request. Raises ServiceError with a friendly message on failure."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    data = None
    h = {"Accept": "application/json", "User-Agent": "Kiln"}
    h.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if auth:
        h["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ServiceError("The service rejected these details. Check them and try again.") from None
        if e.code == 404:
            raise ServiceError("Not found. Check the address or id you entered.") from None
        if e.code == 429:
            raise ServiceError("Too many requests right now. Kiln will try again later.") from None
        raise ServiceError(f"The service answered with an error ({e.code}).") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ServiceError("Cannot reach the service. Check your connection.") from None
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise ServiceError("The service sent something Kiln does not understand.") from None


def base(cfg, default):
    """Service address; `_base` is only there so the tests can point it at a local fake server."""
    return (cfg.get("_base") or default).rstrip("/")


def ts(v):
    """Seconds since the epoch from an ISO string, a date (local midnight) or ms/s numbers."""
    if v in (None, "", 0, "0"):
        return None
    try:
        n = float(v)
        return n / 1000 if n > 1e11 else n
    except (TypeError, ValueError):
        pass
    s = str(v).strip().replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def end_of_day(v):
    """A date-only due value (the user means 'during that day') as local midnight."""
    return ts(v)


def task(service, tid, title, due=None, url="", detail=""):
    return dict(service=service, id=str(tid), title=(title or "(untitled)").strip() or "(untitled)",
                due=due, url=url or "", detail=detail or "")


def event(service, title, start, end=None, url=""):
    return dict(service=service, title=(title or "(no title)").strip() or "(no title)",
                start=start, end=end or start + 1800, url=url or "")


# ----------------------------------------------------------------- services
class Service:
    id = ""
    name = ""
    tagline = ""
    kinds = ("tasks",)          # "tasks" and/or "events"
    color = "5b8cff"
    fields = []
    steps = []                  # how to get the details, one line each
    link = ""                   # opened by "Where do I find this?"
    can_complete = False
    cli = False                 # set up in a terminal instead of a form

    def check(self, cfg):
        """Return a label for the account (a name or an address) or raise ServiceError."""
        raise NotImplementedError

    def fetch(self, cfg):
        """Return {'tasks': [...], 'events': [...]}."""
        raise NotImplementedError

    def complete(self, cfg, tid):
        raise ServiceError("This service does not allow completing tasks from Kiln.")


class Todoist(Service):
    id, name, tagline, color = "todoist", "Todoist", "Tasks due today, overdue and this week", "e44332"
    fields = [Field("token", "API token", secret=True)]
    steps = ["Open Todoist → Settings → Integrations → Developer.", "Copy the API token and paste it here."]
    link = "https://app.todoist.com/app/settings/integrations/developer"
    can_complete = True

    def _get(self, cfg, path, **params):
        return http("GET", base(cfg, "https://api.todoist.com") + path, {"Authorization": f"Bearer {cfg['token']}"},
                    params=params)

    def check(self, cfg):
        self._get(cfg, "/api/v1/tasks/filter", query="today", limit=1)
        return "Todoist"

    def fetch(self, cfg):
        out, cursor = [], None
        for _ in range(3):
            params = {"query": "overdue | today | 7 days", "limit": 50}
            if cursor:
                params["cursor"] = cursor
            d = self._get(cfg, "/api/v1/tasks/filter", **params)
            for t in d.get("results") or []:
                due = t.get("due") or {}
                out.append(task(self.id, t["id"], t.get("content"), ts(due.get("datetime") or due.get("date")),
                                f"https://app.todoist.com/app/task/{t['id']}"))
            cursor = d.get("next_cursor")
            if not cursor:
                break
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        http("POST", base(cfg, "https://api.todoist.com") + f"/api/v1/tasks/{tid}/close",
             {"Authorization": f"Bearer {cfg['token']}"})


class Trello(Service):
    id, name, tagline, color = "trello", "Trello", "Your cards that have a due date", "2f80ed"
    fields = [Field("key", "API key"), Field("token", "Token", secret=True)]
    steps = ["Open the Trello Power-Up admin page and create a Power-Up (any name) to get an API key.",
             "On the same page choose “Token” (next to the key) and allow it.",
             "Paste the key and the token here. Kiln shows cards assigned to you with a due date."]
    link = "https://trello.com/power-ups/admin"
    can_complete = True

    def _get(self, cfg, path, **params):
        params.update(key=cfg["key"].strip(), token=cfg["token"].strip())
        return http("GET", base(cfg, "https://api.trello.com") + path, params=params)

    def check(self, cfg):
        return self._get(cfg, "/1/members/me", fields="username").get("username") or "Trello"

    def fetch(self, cfg):
        boards = {b["id"]: b.get("name", "") for b in self._get(cfg, "/1/members/me/boards", fields="name",
                                                                filter="open")}
        out = []
        limit = time.time() + WEEK
        for c in self._get(cfg, "/1/members/me/cards", filter="open", fields="name,due,dueComplete,url,idBoard"):
            due = ts(c.get("due"))
            if not due or c.get("dueComplete") or due > limit:
                continue
            out.append(task(self.id, c["id"], c.get("name"), due, c.get("url"), boards.get(c.get("idBoard"), "")))
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        http("PUT", base(cfg, "https://api.trello.com") + f"/1/cards/{tid}",
             params={"dueComplete": "true", "key": cfg["key"].strip(), "token": cfg["token"].strip()})


class Linear(Service):
    id, name, tagline, color = "linear", "Linear", "Issues assigned to you", "5e6ad2"
    fields = [Field("token", "Personal API key", secret=True)]
    steps = ["Open Linear → Settings → Account → Security & access.",
             "Under “Personal API keys” create a key and paste it here."]
    link = "https://linear.app/settings/account/security"
    can_complete = True

    def _gql(self, cfg, query, variables=None):
        d = http("POST", base(cfg, "https://api.linear.app") + "/graphql", {"Authorization": cfg["token"].strip()},
                 body={"query": query, "variables": variables or {}})
        if d.get("errors") and not d.get("data"):
            raise ServiceError("Linear refused the request. Check the API key.")
        return d.get("data") or {}

    def check(self, cfg):
        return (self._gql(cfg, "{ viewer { name } }").get("viewer") or {}).get("name") or "Linear"

    def fetch(self, cfg):
        q = """{ viewer { assignedIssues(first: 50, filter: {state: {type: {nin: ["completed", "canceled"]}}}) {
                 nodes { id identifier title url dueDate state { name } } } } }"""
        nodes = (((self._gql(cfg, q).get("viewer") or {}).get("assignedIssues")) or {}).get("nodes") or []
        return {"tasks": [task(self.id, n["id"], n.get("title"), ts(n.get("dueDate")), n.get("url"),
                               f"{n.get('identifier', '')} · {(n.get('state') or {}).get('name', '')}".strip(" ·"))
                          for n in nodes], "events": []}

    def complete(self, cfg, tid):
        q = "query($id: String!) { issue(id: $id) { team { states { nodes { id type } } } } }"
        states = ((((self._gql(cfg, q, {"id": tid}).get("issue") or {}).get("team")) or {}).get("states") or {}) \
            .get("nodes") or []
        done = next((s["id"] for s in states if s.get("type") == "completed"), None)
        if not done:
            raise ServiceError("This team has no “done” state.")
        self._gql(cfg, "mutation($id: String!, $s: String!) { issueUpdate(id: $id, input: {stateId: $s}) "
                       "{ success } }", {"id": tid, "s": done})


class Asana(Service):
    id, name, tagline, color = "asana", "Asana", "Tasks assigned to you", "f06a6a"
    fields = [Field("token", "Personal access token", secret=True)]
    steps = ["Open the Asana developer console → Personal access token.",
             "Create a token and paste it here."]
    link = "https://app.asana.com/0/my-apps"
    can_complete = True

    def _get(self, cfg, path, **params):
        return http("GET", base(cfg, "https://app.asana.com") + "/api/1.0" + path,
                    {"Authorization": f"Bearer {cfg['token'].strip()}"}, params=params)

    def check(self, cfg):
        return (self._get(cfg, "/users/me").get("data") or {}).get("name") or "Asana"

    def fetch(self, cfg):
        me = self._get(cfg, "/users/me").get("data") or {}
        out = []
        for ws in (me.get("workspaces") or [])[:3]:
            d = self._get(cfg, "/tasks", assignee="me", workspace=ws["gid"], completed_since="now", limit=50,
                          opt_fields="name,due_on,due_at,permalink_url,completed")
            for t in d.get("data") or []:
                if t.get("completed"):
                    continue
                out.append(task(self.id, t["gid"], t.get("name"), ts(t.get("due_at") or t.get("due_on")),
                                t.get("permalink_url"), ws.get("name", "")))
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        http("PUT", base(cfg, "https://app.asana.com") + f"/api/1.0/tasks/{tid}",
             {"Authorization": f"Bearer {cfg['token'].strip()}"}, body={"data": {"completed": True}})


class Jira(Service):
    id, name, tagline, color = "jira", "Jira", "Open issues assigned to you", "2684ff"
    fields = [Field("site", "Site", hint="yourteam.atlassian.net"), Field("email", "Account email"),
              Field("token", "API token", secret=True)]
    steps = ["Open your Atlassian account → Security → API tokens and create a token.",
             "Enter your site (like yourteam.atlassian.net), your email and the token."]
    link = "https://id.atlassian.com/manage-profile/security/api-tokens"
    can_complete = True

    @staticmethod
    def host(cfg):
        h = re.sub(r"^https?://", "", cfg["site"].strip()).strip("/")
        return h if "." in h else h + ".atlassian.net"

    def _url(self, cfg, path):
        return base(cfg, f"https://{self.host(cfg)}") + path

    def _auth(self, cfg):
        return (cfg["email"].strip(), cfg["token"].strip())

    def check(self, cfg):
        return http("GET", self._url(cfg, "/rest/api/3/myself"), auth=self._auth(cfg)).get("displayName") or "Jira"

    def fetch(self, cfg):
        jql = "assignee = currentUser() AND statusCategory != Done ORDER BY duedate ASC, updated DESC"
        d = http("GET", self._url(cfg, "/rest/api/3/search/jql"), auth=self._auth(cfg),
                 params={"jql": jql, "fields": "summary,duedate,status", "maxResults": 40})
        out = []
        for i in d.get("issues") or []:
            f = i.get("fields") or {}
            out.append(task(self.id, i["key"], f.get("summary"), ts(f.get("duedate")),
                            f"https://{self.host(cfg)}/browse/{i['key']}",
                            f"{i['key']} · {(f.get('status') or {}).get('name', '')}".strip(" ·")))
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        d = http("GET", self._url(cfg, f"/rest/api/3/issue/{tid}/transitions"), auth=self._auth(cfg))
        t = next((t for t in d.get("transitions") or []
                  if ((t.get("to") or {}).get("statusCategory") or {}).get("key") == "done"), None)
        if not t:
            raise ServiceError("This issue has no way to move to Done from its current status.")
        http("POST", self._url(cfg, f"/rest/api/3/issue/{tid}/transitions"), auth=self._auth(cfg),
             body={"transition": {"id": t["id"]}})


class Notion(Service):
    id, name, tagline, color = "notion", "Notion", "Open items of one task database", "9b9a97"
    fields = [Field("token", "Integration token", secret=True),
              Field("database", "Database link", hint="paste the link of your tasks database")]
    steps = ["Open notion.so/profile/integrations, create an internal integration and copy its token.",
             "Open your tasks database in Notion → ••• → Connections → add your integration.",
             "Copy the database link (••• → Copy link) and paste it here."]
    link = "https://www.notion.so/profile/integrations"
    can_complete = True
    VERSION = "2022-06-28"

    def _h(self, cfg):
        return {"Authorization": f"Bearer {cfg['token'].strip()}", "Notion-Version": self.VERSION}

    @staticmethod
    def db_id(cfg):
        s = (cfg.get("database") or "").split("?")[0]
        m = re.findall(r"[0-9a-fA-F]{32}", s.replace("-", ""))
        if not m:
            raise ServiceError("That does not look like a Notion database link.")
        return m[-1]

    def _schema(self, cfg):
        d = http("GET", base(cfg, "https://api.notion.com") + f"/v1/databases/{self.db_id(cfg)}", self._h(cfg))
        props = d.get("properties") or {}
        title = next((k for k, v in props.items() if v.get("type") == "title"), None)
        dates = [k for k, v in props.items() if v.get("type") == "date"]
        date = next((k for k in dates if k.lower() in ("due", "due date", "date", "deadline")), dates[0] if dates else None)
        done = None
        for k, v in props.items():
            if v.get("type") == "checkbox":
                done = (k, "checkbox", None)
                break
        if not done:
            for k, v in props.items():
                if v.get("type") == "status":
                    st = v["status"]
                    ids = {i for g in st.get("groups", []) if g.get("name") == "Complete" for i in g.get("option_ids", [])}
                    names = [o["name"] for o in st.get("options", []) if o.get("id") in ids]
                    done = (k, "status", names)
                    break
        name = "".join(t.get("plain_text", "") for t in (d.get("title") or [])) or "Notion"
        return title, date, done, name

    def check(self, cfg):
        return self._schema(cfg)[3]

    def fetch(self, cfg):
        title, date, done, _name = self._schema(cfg)
        body = {"page_size": 50}
        if done and done[1] == "checkbox":
            body["filter"] = {"property": done[0], "checkbox": {"equals": False}}
        elif done and done[2]:
            conds = [{"property": done[0], "status": {"does_not_equal": n}} for n in done[2]]
            body["filter"] = conds[0] if len(conds) == 1 else {"and": conds}
        if date:
            body["sorts"] = [{"property": date, "direction": "ascending"}]
        d = http("POST", base(cfg, "https://api.notion.com") + f"/v1/databases/{self.db_id(cfg)}/query",
                 self._h(cfg), body=body)
        out = []
        for p in d.get("results") or []:
            props = p.get("properties") or {}
            name = "".join(t.get("plain_text", "") for t in ((props.get(title) or {}).get("title") or []))
            due = ts(((props.get(date) or {}).get("date") or {}).get("start")) if date else None
            out.append(task(self.id, p["id"], name, due, p.get("url")))
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        _t, _d, done, _n = self._schema(cfg)
        if not done:
            raise ServiceError("This database has no checkbox or status to mark as done.")
        if done[1] == "checkbox":
            props = {done[0]: {"checkbox": True}}
        elif done[2]:
            props = {done[0]: {"status": {"name": done[2][0]}}}
        else:
            raise ServiceError("This database has no “Complete” status.")
        http("PATCH", base(cfg, "https://api.notion.com") + f"/v1/pages/{tid}", self._h(cfg),
             body={"properties": props})


class ClickUp(Service):
    id, name, tagline, color = "clickup", "ClickUp", "Open tasks assigned to you", "7b68ee"
    fields = [Field("token", "Personal API token", secret=True)]
    steps = ["Open ClickUp → your avatar → Settings → Apps.", "Generate a personal API token and paste it here."]
    link = "https://app.clickup.com/settings/apps"
    can_complete = True

    def _get(self, cfg, path, **params):
        return http("GET", base(cfg, "https://api.clickup.com") + "/api/v2" + path,
                    {"Authorization": cfg["token"].strip()}, params=params)

    def check(self, cfg):
        return (self._get(cfg, "/user").get("user") or {}).get("username") or "ClickUp"

    def fetch(self, cfg):
        uid = (self._get(cfg, "/user").get("user") or {}).get("id")
        out = []
        for team in (self._get(cfg, "/team").get("teams") or [])[:2]:
            d = self._get(cfg, f"/team/{team['id']}/task", **{"assignees[]": uid, "include_closed": "false",
                                                              "order_by": "due_date", "subtasks": "true"})
            for t in d.get("tasks") or []:
                if (t.get("status") or {}).get("type") == "closed":
                    continue
                out.append(task(self.id, t["id"], t.get("name"), ts(t.get("due_date")), t.get("url"),
                                (t.get("list") or {}).get("name", "")))
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        lst = ((self._get(cfg, f"/task/{tid}").get("list")) or {}).get("id")
        states = self._get(cfg, f"/list/{lst}").get("statuses") or []
        done = next((s["status"] for s in states if s.get("type") == "closed"), None)
        if not done:
            raise ServiceError("This list has no closed status.")
        http("PUT", base(cfg, "https://api.clickup.com") + f"/api/v2/task/{tid}",
             {"Authorization": cfg["token"].strip()}, body={"status": done})


class Slack(Service):
    id, name, tagline, color = "slack", "Slack", "Your reminders (“remind me to…”)", "4a154b"
    fields = [Field("token", "User token", secret=True, hint="starts with xoxp-")]
    steps = ["Create a Slack app from the manifest in docs/ACCOUNTS.md (one click, no code).",
             "Install it to your workspace and copy the “User OAuth Token”.",
             "Paste it here. Kiln lists your Slack reminders as tasks."]
    link = "https://api.slack.com/apps?new_app=1"
    can_complete = True

    def _call(self, cfg, method, **form):
        d = http("POST", base(cfg, "https://slack.com") + f"/api/{method}",
                 {"Authorization": f"Bearer {cfg['token'].strip()}"}, form=form)
        if not d.get("ok"):
            err = d.get("error", "")
            if err in ("invalid_auth", "not_authed", "token_revoked", "account_inactive"):
                raise ServiceError("Slack rejected this token.")
            if err == "missing_scope":
                raise ServiceError("The token needs the reminders:read and reminders:write scopes.")
            raise ServiceError(f"Slack said: {err or 'error'}.")
        return d

    def check(self, cfg):
        d = self._call(cfg, "auth.test")
        self._call(cfg, "reminders.list")
        return f"{d.get('user', '')} @ {d.get('team', 'Slack')}".strip(" @")

    def fetch(self, cfg):
        out = []
        for r in self._call(cfg, "reminders.list").get("reminders") or []:
            if r.get("complete_ts"):
                continue
            out.append(task(self.id, r["id"], r.get("text"), ts(r.get("time")), "https://app.slack.com/"))
        return {"tasks": out, "events": []}

    def complete(self, cfg, tid):
        self._call(cfg, "reminders.complete", reminder=tid)


class Calendar(Service):
    id, name, tagline, color = "calendar", "Calendar", "Meetings from Google, Outlook, Apple or any iCal link", "34a853"
    kinds = ("events",)
    fields = [Field("urls", "iCal links", secret=True, multiline=True,
                    hint="one per line (a calendar's secret address in iCal format)")]
    steps = ["Google Calendar: Settings → your calendar → “Secret address in iCal format”.",
             "Outlook: Settings → Calendar → Shared calendars → Publish a calendar → ICS link.",
             "Apple/iCloud: share the calendar publicly and copy its link.",
             "Paste one link per line. Keep them private: anyone with the link can read the calendar."]
    link = "https://support.google.com/calendar/answer/37648"

    def urls(self, cfg):
        out = []
        for line in (cfg.get("urls") or "").splitlines():
            u = line.strip().replace("webcal://", "https://", 1)
            if u:
                out.append(u)
        if not out:
            raise ServiceError("Paste at least one iCal link.")
        return out

    def _download(self, cfg, url):
        req = urllib.request.Request(url, headers={"User-Agent": "Kiln"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read(8_000_000)
        except urllib.error.HTTPError:
            raise ServiceError("A calendar link was rejected. Copy it again from the calendar settings.") from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise ServiceError("Cannot reach the calendar. Check your connection.") from None
        text = raw.decode("utf-8", "replace")
        if "BEGIN:VCALENDAR" not in text:
            raise ServiceError("That link is not an iCal calendar.")
        return text

    def check(self, cfg):
        urls = self.urls(cfg)
        for u in urls:
            self._download(cfg, u)
        return f"{len(urls)} calendar" + ("s" if len(urls) != 1 else "")

    def fetch(self, cfg):
        now = datetime.datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=2)
        out = []
        for u in self.urls(cfg):
            out += ics_events(self._download(cfg, u), start, end, self.id)
        out.sort(key=lambda e: e["start"])
        return {"tasks": [], "events": out}


class Lark(Service):
    id, name, tagline, color = "lark", "Lark", "Tasks and meetings through the official lark-cli", "00d6b9"
    kinds = ("tasks", "events")
    cli = True
    steps = ["Install lark-cli (github.com/larksuite/cli).", "lark-cli config init --new --brand lark",
             "lark-cli auth login --domain task,calendar"]
    link = "https://github.com/larksuite/cli"
    can_complete = True
    BIN = shutil.which("lark-cli") or f"{HOME}/.local/bin/lark-cli"

    def _run(self, *args):
        if not (shutil.which("lark-cli") or os.path.exists(self.BIN)):
            raise ServiceError("lark-cli is not installed.")
        try:
            r = subprocess.run([shutil.which("lark-cli") or self.BIN, *args], capture_output=True, text=True,
                               timeout=40)
        except (OSError, subprocess.TimeoutExpired):
            raise ServiceError("lark-cli did not answer.") from None
        try:
            d = json.loads(r.stdout or r.stderr)
        except ValueError:
            raise ServiceError("lark-cli sent something Kiln does not understand.") from None
        if isinstance(d, dict) and d.get("ok") is False:
            e = d.get("error") or {}
            if e.get("subtype") == "not_configured" or e.get("type") == "config":
                raise ServiceError("Run: lark-cli config init --new --brand lark")
            raise ServiceError("Sign in with: lark-cli auth login --domain task,calendar")
        return d

    def check(self, cfg):
        self._run("task", "+get-my-tasks", "--as", "user", "--complete=false", "--format", "json")
        return "Lark"

    @staticmethod
    def _walk(o):
        if isinstance(o, dict):
            yield o
            for v in o.values():
                yield from Lark._walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from Lark._walk(v)

    def fetch(self, cfg):
        out_t, out_e, seen = [], [], set()
        for d in self._walk(self._run("task", "+get-my-tasks", "--as", "user", "--complete=false", "--format", "json")):
            guid = d.get("guid") or d.get("task_guid")
            title = d.get("summary") or d.get("title")
            if not guid or not isinstance(title, str) or guid in seen or ts(d.get("completed_at")):
                continue
            seen.add(guid)
            url = d.get("url") or d.get("applink") or d.get("app_link") or ""
            due = d.get("due") or d.get("due_time") or d.get("due_at")
            if isinstance(due, dict):
                due = due.get("timestamp") or due.get("date")
            out_t.append(task(self.id, guid, title, ts(due), url if isinstance(url, str) else ""))
        try:
            agenda = self._run("calendar", "+agenda", "--as", "user", "--format", "json")
        except ServiceError:
            agenda = None
        for d in self._walk(agenda):
            title = d.get("summary") or d.get("title")
            start = d.get("start_time") or d.get("start")
            if not isinstance(title, str) or start is None or d.get("status") == "cancelled":
                continue
            if isinstance(start, dict):
                start = start.get("timestamp") or start.get("date_time") or start.get("date")
            s = ts(start)
            if not s:
                continue
            e = d.get("end_time") or d.get("end")
            if isinstance(e, dict):
                e = e.get("timestamp") or e.get("date_time") or e.get("date")
            eid = d.get("event_id") or (title, s)
            if eid in seen:
                continue
            seen.add(eid)
            vchat = d.get("vchat") if isinstance(d.get("vchat"), dict) else {}
            url = vchat.get("meeting_url") or d.get("meeting_url") or d.get("app_link") or d.get("applink") or ""
            out_e.append(event(self.id, title, s, ts(e), url if isinstance(url, str) else ""))
        return {"tasks": out_t, "events": out_e}

    def complete(self, cfg, tid):
        self._run("task", "+complete", "--as", "user", "--task-id", tid)


SERVICES = {s.id: s for s in (Calendar(), Todoist(), Trello(), Linear(), Asana(), Jira(), Notion(), ClickUp(),
                              Slack(), Lark())}


# ----------------------------------------------------------------- iCal
WINDOWS_TZ = {
    "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris", "GMT Standard Time": "Europe/London", "Egypt Standard Time": "Africa/Cairo",
    "Arab Standard Time": "Asia/Riyadh", "Arabian Standard Time": "Asia/Dubai", "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago", "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles", "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo", "Turkey Standard Time": "Europe/Istanbul",
    "E. Europe Standard Time": "Europe/Chisinau", "South Africa Standard Time": "Africa/Johannesburg",
    "AUS Eastern Standard Time": "Australia/Sydney", "UTC": "UTC",
}
MEETING_RE = re.compile(r"https://[^\s<>\"'\\]*(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|"
                        r"webex\.com|whereby\.com|around\.co|chime\.aws|lark(?:suite)?\.com/(?:vc|calendar))[^\s<>\"'\\]*")


def _unfold(text):
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _unescape(s):
    return s.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _tz(name):
    import zoneinfo
    for n in (name, WINDOWS_TZ.get(name or "", "")):
        if n:
            try:
                return zoneinfo.ZoneInfo(n)
            except Exception:                          # unknown zone name
                pass
    return None


def _when(value, params):
    """(aware datetime, all_day) for a DTSTART/DTEND/EXDATE/RECURRENCE-ID value."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        d = datetime.datetime.strptime(v[:8], "%Y%m%d")
        return d.astimezone(), True
    if v.endswith("Z"):
        return datetime.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc), False
    d = datetime.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    zone = _tz(params.get("TZID"))
    return (d.replace(tzinfo=zone) if zone else d.astimezone()), False


def _props(lines):
    """Properties of one component as a list of (NAME, {params}, value)."""
    out = []
    for line in lines:
        if ":" not in line:
            continue
        head, _, value = line.partition(":")
        parts = head.split(";")
        params = {}
        for p in parts[1:]:
            k, _, val = p.partition("=")
            params[k.upper()] = val.strip('"')
        out.append((parts[0].upper(), params, value))
    return out


def _duration(v):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return datetime.timedelta(minutes=30)
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * datetime.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def ics_events(text, win_start, win_end, service="calendar"):
    """Events of an iCal feed that overlap [win_start, win_end), recurring ones expanded. All-day events
    are left out (holidays and birthdays would crowd a meeting list)."""
    try:
        from dateutil import rrule
    except ImportError:
        rrule = None
    comps, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = []
        elif line == "END:VEVENT" and cur is not None:
            comps.append(_props(cur))
            cur = None
        elif cur is not None:
            cur.append(line)
    singles, masters, skip = [], [], set()
    for props in comps:
        p = {}
        for name, params, value in props:
            p.setdefault(name, []).append((params, value))
        if "DTSTART" not in p or (p.get("STATUS") or [({}, "")])[0][1].upper() == "CANCELLED":
            continue
        try:
            start, all_day = _when(p["DTSTART"][0][1], p["DTSTART"][0][0])
            if all_day:
                continue
            if "DTEND" in p:
                end, _ = _when(p["DTEND"][0][1], p["DTEND"][0][0])
            elif "DURATION" in p:
                end = start + _duration(p["DURATION"][0][1])
            else:
                end = start + datetime.timedelta(minutes=30)
        except (ValueError, KeyError):
            continue
        blob = _unescape(" ".join(v for k in ("URL", "LOCATION", "DESCRIPTION") for _pp, v in p.get(k, [])))
        m = MEETING_RE.search(blob)
        info = dict(title=_unescape((p.get("SUMMARY") or [({}, "")])[0][1]), url=m.group(0) if m else "",
                    uid=(p.get("UID") or [({}, "")])[0][1], dur=end - start)
        if "RECURRENCE-ID" in p:
            try:
                skip.add((info["uid"], _when(p["RECURRENCE-ID"][0][1], p["RECURRENCE-ID"][0][0])[0]))
            except ValueError:
                pass
        if "RRULE" in p and rrule:
            ex = set()
            for params, value in p.get("EXDATE", []):
                for one in value.split(","):
                    try:
                        ex.add(_when(one, params)[0])
                    except ValueError:
                        pass
            masters.append((start, p["RRULE"][0][1], ex, info))
        elif end > win_start and start < win_end:
            singles.append((start, end, info))
    out = [(s, e, i) for s, e, i in singles]
    for start, rule, ex, info in masters:
        rule = re.sub(r"UNTIL=(\d{8}T\d{6})(?![\dZ])", r"UNTIL=\1Z", rule)
        rule = re.sub(r"UNTIL=(\d{8})(?![\dT])", r"UNTIL=\1T235959Z", rule)
        try:
            r = rrule.rrulestr(rule, dtstart=start)
            for occ in r.between(win_start - info["dur"], win_end, inc=True):
                if occ in ex or (info["uid"], occ) in skip:
                    continue
                out.append((occ, occ + info["dur"], info))
        except (ValueError, TypeError, OverflowError):
            continue
    result = [event(service, i["title"], s.timestamp(), e.timestamp(), i["url"]) for s, e, i in out
              if e > win_start and s < win_end]
    result.sort(key=lambda ev: ev["start"])
    return result


# ----------------------------------------------------------------- the cache the cards read
def read_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def sync(only=None):
    """Fetch every connected service and rewrite the cache. A service that fails keeps its last data."""
    old = read_cache().get("services", {})
    result = {}
    ids = connected()
    for sid in ids:
        if only and sid != only:
            result[sid] = old.get(sid, {})
            continue
        svc = SERVICES[sid]
        cfg = details(sid)
        entry = dict(old.get(sid) or {})
        try:
            data = svc.fetch(cfg)
            entry.update(ok=True, error=None, tasks=data["tasks"], events=data["events"], at=time.time())
        except ServiceError as e:
            entry.update(ok=False, error=str(e), at=time.time())
            entry.setdefault("tasks", [])
            entry.setdefault("events", [])
        except Exception as e:                            # a service must never take the others down
            entry.update(ok=False, error=f"Unexpected problem ({type(e).__name__}).", at=time.time())
            entry.setdefault("tasks", [])
            entry.setdefault("events", [])
        result[sid] = entry
    os.makedirs(CACHE, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"updated": time.time(), "services": result}, f)
    os.replace(tmp, CACHE_FILE)
    return result


def missing(service, values):
    return [f.label for f in SERVICES[service].fields if not f.optional and not (values.get(f.key) or "").strip()]


def test(service, values):
    """Check details that are not saved yet. Returns the account label or raises ServiceError."""
    gap = missing(service, values)
    if gap:
        raise ServiceError("Fill in: " + ", ".join(gap) + ".")
    return SERVICES[service].check(values)


# ----------------------------------------------------------------- command line
def _ask(svc):
    print(f"\n{svc.name}: {svc.tagline}\n")
    for i, s in enumerate(svc.steps, 1):
        print(f"  {i}. {s}")
    if svc.link:
        print(f"\n  {svc.link}\n")
    values = {}
    for f in svc.fields:
        prompt = f"{f.label}" + (f" ({f.hint})" if f.hint else "") + ": "
        if f.multiline:
            print(prompt + "(one per line, empty line to finish)")
            lines = []
            while True:
                line = input("  ").strip()
                if not line:
                    break
                lines.append(line)
            values[f.key] = "\n".join(lines)
        else:
            values[f.key] = (getpass.getpass(prompt) if f.secret else input(prompt)).strip()
    return values


def main(argv):
    cmd = argv[0] if argv else "list"
    name = argv[1] if len(argv) > 1 else None
    if cmd in ("connect", "disconnect", "test", "complete") and name not in SERVICES:
        print("Services: " + ", ".join(SERVICES), file=sys.stderr)
        return 2
    try:
        if cmd == "list":
            ids = connected()
            cache = read_cache().get("services", {})
            for sid, s in SERVICES.items():
                st = "connected" if sid in ids else "-"
                c = cache.get(sid) or {}
                if sid in ids and c.get("error"):
                    st = "problem: " + c["error"]
                elif sid in ids and c:
                    st = f"connected · {len(c.get('tasks', []))} tasks · {len(c.get('events', []))} events"
                print(f"{sid:10} {s.name:10} {st}")
        elif cmd == "connect":
            svc = SERVICES[name]
            if svc.cli:
                print("\n".join(["", f"{svc.name} is set up in a terminal:"] + [f"  {s}" for s in svc.steps]))
                print("\nThen turn it on with: kiln config  (set integrations.lark to true)")
                return 0
            values = _ask(svc)
            print("Checking…", end=" ", flush=True)
            label = test(name, values)
            data = load_accounts()
            data[name] = values
            save_accounts(data)
            print(f"connected ({label}).")
            sync(name)
        elif cmd == "disconnect":
            data = load_accounts()
            data.pop(name, None)
            save_accounts(data)
            sync()
            print(f"{SERVICES[name].name} disconnected.")
        elif cmd == "test":
            print("OK: " + SERVICES[name].check(details(name)))
        elif cmd == "sync":
            sync(name)
        elif cmd == "complete":
            SERVICES[name].complete(details(name), argv[2])
            sync(name)
        else:
            print(__doc__)
            return 2
    except ServiceError as e:
        print(f"{e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
