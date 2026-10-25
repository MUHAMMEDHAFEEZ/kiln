"""Tests for share/accounts.py against a local fake server (no network, no real accounts).

    python3 -m unittest discover -s tests
"""
import datetime
import http.server
import json
import os
import sys
import threading
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import accounts as A  # noqa: E402


class Fake(http.server.BaseHTTPRequestHandler):
    routes = {}          # (METHOD, path) -> object or callable(handler, query, body) -> object
    seen = []            # (METHOD, path, query, body, headers)

    def log_message(self, *_):
        pass

    def _go(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        try:
            body = json.loads(raw) if raw and raw.lstrip()[:1] in "{[" else raw
        except ValueError:
            body = raw
        q = urllib.parse.parse_qs(u.query)
        Fake.seen.append((self.command, u.path, q, body, dict(self.headers)))
        r = Fake.routes.get((self.command, u.path))
        if r is None:
            self.send_response(404)
            self.end_headers()
            return
        out = r(self, q, body) if callable(r) else r
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = _go


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Fake.routes, Fake.seen = {}, []

    def cfg(self, **kw):
        return dict(_base=self.base, **kw)

    def test_todoist(self):
        Fake.routes[("GET", "/api/v1/tasks/filter")] = {"results": [
            {"id": "7", "content": "Pay rent", "due": {"date": "2030-01-02"}},
            {"id": "8", "content": "Call Sam", "due": {"datetime": "2030-01-02T09:30:00Z"}}], "next_cursor": None}
        Fake.routes[("POST", "/api/v1/tasks/7/close")] = {}
        s = A.SERVICES["todoist"]
        got = s.fetch(self.cfg(token="abc"))["tasks"]
        self.assertEqual([t["title"] for t in got], ["Pay rent", "Call Sam"])
        self.assertTrue(got[0]["due"] and got[1]["due"])
        self.assertEqual(Fake.seen[0][4]["Authorization"], "Bearer abc")
        s.complete(self.cfg(token="abc"), "7")
        self.assertEqual(Fake.seen[-1][:2], ("POST", "/api/v1/tasks/7/close"))

    def test_trello_only_due_cards_this_week(self):
        soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).isoformat()
        far = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)).isoformat()
        Fake.routes[("GET", "/1/members/me/boards")] = [{"id": "b1", "name": "Launch"}]
        Fake.routes[("GET", "/1/members/me/cards")] = [
            {"id": "c1", "name": "Ship it", "due": soon, "dueComplete": False, "url": "u1", "idBoard": "b1"},
            {"id": "c2", "name": "Later", "due": far, "dueComplete": False, "idBoard": "b1"},
            {"id": "c3", "name": "Done", "due": soon, "dueComplete": True, "idBoard": "b1"},
            {"id": "c4", "name": "No date", "due": None, "idBoard": "b1"}]
        got = A.SERVICES["trello"].fetch(self.cfg(key="k", token="t"))["tasks"]
        self.assertEqual([(t["title"], t["detail"]) for t in got], [("Ship it", "Launch")])
        self.assertEqual(Fake.seen[0][2]["key"], ["k"])

    def test_linear_and_complete(self):
        def gql(_h, _q, body):
            if "assignedIssues" in body["query"]:
                return {"data": {"viewer": {"assignedIssues": {"nodes": [
                    {"id": "i1", "identifier": "ENG-4", "title": "Fix login", "url": "u", "dueDate": None,
                     "state": {"name": "In Progress"}}]}}}}
            if "issueUpdate" in body["query"]:
                return {"data": {"issueUpdate": {"success": True}}}
            return {"data": {"issue": {"team": {"states": {"nodes": [
                {"id": "s1", "type": "started"}, {"id": "s2", "type": "completed"}]}}}}}
        Fake.routes[("POST", "/graphql")] = gql
        s = A.SERVICES["linear"]
        t = s.fetch(self.cfg(token="lin_key"))["tasks"][0]
        self.assertEqual((t["title"], t["detail"]), ("Fix login", "ENG-4 · In Progress"))
        self.assertEqual(Fake.seen[0][4]["Authorization"], "lin_key")        # no "Bearer " for Linear
        s.complete(self.cfg(token="lin_key"), "i1")
        self.assertEqual(Fake.seen[-1][3]["variables"], {"id": "i1", "s": "s2"})

    def test_asana(self):
        Fake.routes[("GET", "/api/1.0/users/me")] = {"data": {"name": "Ada", "workspaces": [{"gid": "w1", "name": "Co"}]}}
        Fake.routes[("GET", "/api/1.0/tasks")] = {"data": [
            {"gid": "t1", "name": "Draft brief", "due_on": "2030-03-01", "permalink_url": "p", "completed": False},
            {"gid": "t2", "name": "Old", "completed": True}]}
        got = A.SERVICES["asana"].fetch(self.cfg(token="x"))["tasks"]
        self.assertEqual([t["title"] for t in got], ["Draft brief"])
        self.assertEqual(Fake.seen[-1][2]["completed_since"], ["now"])

    def test_jira_search_and_transition(self):
        Fake.routes[("GET", "/rest/api/3/search/jql")] = {"issues": [
            {"key": "OPS-9", "fields": {"summary": "Rotate keys", "duedate": "2030-02-02", "status": {"name": "To Do"}}}]}
        Fake.routes[("GET", "/rest/api/3/issue/OPS-9/transitions")] = {"transitions": [
            {"id": "11", "to": {"statusCategory": {"key": "indeterminate"}}},
            {"id": "31", "to": {"statusCategory": {"key": "done"}}}]}
        Fake.routes[("POST", "/rest/api/3/issue/OPS-9/transitions")] = {}
        cfg = self.cfg(site="team.atlassian.net", email="a@b.co", token="tok")
        s = A.SERVICES["jira"]
        t = s.fetch(cfg)["tasks"][0]
        self.assertEqual((t["id"], t["url"]), ("OPS-9", "https://team.atlassian.net/browse/OPS-9"))
        self.assertTrue(Fake.seen[0][4]["Authorization"].startswith("Basic "))
        s.complete(cfg, "OPS-9")
        self.assertEqual(Fake.seen[-1][3], {"transition": {"id": "31"}})
        self.assertEqual(A.Jira.host({"site": "https://acme/"}), "acme.atlassian.net")

    def test_notion_checkbox_and_status(self):
        db = "0123456789abcdef0123456789abcdef"
        link = f"https://www.notion.so/ws/Tasks-{db}?v=abc"
        schema = {"title": [{"plain_text": "Tasks"}], "properties": {
            "Name": {"type": "title"}, "Due": {"type": "date"},
            "Status": {"type": "status", "status": {
                "options": [{"id": "a", "name": "Todo"}, {"id": "b", "name": "Done"}],
                "groups": [{"name": "To-do", "option_ids": ["a"]}, {"name": "Complete", "option_ids": ["b"]}]}}}}
        Fake.routes[("GET", f"/v1/databases/{db}")] = schema
        Fake.routes[("POST", f"/v1/databases/{db}/query")] = {"results": [
            {"id": "p1", "url": "pu", "properties": {"Name": {"title": [{"plain_text": "Write docs"}]},
                                                       "Due": {"date": {"start": "2030-05-05"}}}}]}
        Fake.routes[("PATCH", "/v1/pages/p1")] = {}
        cfg = self.cfg(token="ntn_x", database=link)
        s = A.SERVICES["notion"]
        self.assertEqual(s.check(cfg), "Tasks")
        t = s.fetch(cfg)["tasks"][0]
        self.assertEqual(t["title"], "Write docs")
        q = [r for r in Fake.seen if r[0] == "POST"][0][3]
        self.assertEqual(q["filter"], {"property": "Status", "status": {"does_not_equal": "Done"}})
        s.complete(cfg, "p1")
        self.assertEqual(Fake.seen[-1][3], {"properties": {"Status": {"status": {"name": "Done"}}}})
        with self.assertRaises(A.ServiceError):
            A.Notion.db_id({"database": "not a link"})

    def test_clickup(self):
        Fake.routes[("GET", "/api/v2/user")] = {"user": {"id": 5, "username": "ada"}}
        Fake.routes[("GET", "/api/v2/team")] = {"teams": [{"id": "9"}]}
        Fake.routes[("GET", "/api/v2/team/9/task")] = {"tasks": [
            {"id": "x1", "name": "Plan sprint", "due_date": "1893456000000", "url": "cu", "status": {"type": "open"},
             "list": {"name": "Sprint"}},
            {"id": "x2", "name": "Shipped", "status": {"type": "closed"}}]}
        got = A.SERVICES["clickup"].fetch(self.cfg(token="pk_1"))["tasks"]
        self.assertEqual([(t["title"], t["detail"]) for t in got], [("Plan sprint", "Sprint")])
        self.assertEqual(Fake.seen[-1][2]["assignees[]"], ["5"])

    def test_slack_reminders_and_errors(self):
        Fake.routes[("POST", "/api/auth.test")] = {"ok": True, "user": "ada", "team": "Acme"}
        Fake.routes[("POST", "/api/reminders.list")] = {"ok": True, "reminders": [
            {"id": "Rm1", "text": "Send invoice", "time": 1893456000, "complete_ts": 0},
            {"id": "Rm2", "text": "Done one", "time": 1, "complete_ts": 5}]}
        Fake.routes[("POST", "/api/reminders.complete")] = {"ok": True}
        s = A.SERVICES["slack"]
        self.assertEqual(s.check(self.cfg(token="xoxp-1")), "ada @ Acme")
        self.assertEqual([t["title"] for t in s.fetch(self.cfg(token="xoxp-1"))["tasks"]], ["Send invoice"])
        s.complete(self.cfg(token="xoxp-1"), "Rm1")
        self.assertEqual(Fake.seen[-1][3], "reminder=Rm1")
        Fake.routes[("POST", "/api/auth.test")] = {"ok": False, "error": "invalid_auth"}
        with self.assertRaises(A.ServiceError):
            s.check(self.cfg(token="bad"))

    def test_http_errors_are_friendly_and_secret_free(self):
        with self.assertRaises(A.ServiceError) as c:               # 404 from the fake server
            A.SERVICES["asana"].check(self.cfg(token="SECRET-TOKEN"))
        self.assertNotIn("SECRET-TOKEN", str(c.exception))
        with self.assertRaises(A.ServiceError) as c:               # nothing listens here
            A.SERVICES["asana"].check({"token": "SECRET-TOKEN", "_base": "http://127.0.0.1:9"})
        self.assertIn("Cannot reach", str(c.exception))

    def test_form_validation(self):
        with self.assertRaises(A.ServiceError):
            A.test("jira", {"site": "x", "email": "", "token": ""})


ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
DTSTART:{d}T090000Z
DTEND:{d}T093000Z
SUMMARY:Planning
DESCRIPTION:Join https://meet.google.com/abc-defg-hij\\nthanks
END:VEVENT
BEGIN:VEVENT
UID:daily
DTSTART:{y}T100000Z
DTEND:{y}T101500Z
RRULE:FREQ=DAILY
EXDATE:{x}T100000Z
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:daily
RECURRENCE-ID:{t}T100000Z
DTSTART:{t}T130000Z
DTEND:{t}T133000Z
SUMMARY:Standup (moved)
END:VEVENT
BEGIN:VEVENT
UID:hol
DTSTART;VALUE=DATE:{d}
SUMMARY:Public holiday
END:VEVENT
BEGIN:VEVENT
UID:gone
DTSTART:{d}T150000Z
DTEND:{d}T160000Z
STATUS:CANCELLED
SUMMARY:Cancelled
END:VEVENT
BEGIN:VEVENT
UID:tz
DTSTART;TZID=Europe/Berlin:{d}T180000
DTEND;TZID=Europe/Berlin:{d}T190000
SUMMARY:Berlin sync with a very
  long folded title
END:VEVENT
END:VCALENDAR
"""


class IcsTests(unittest.TestCase):
    def test_events_recurrence_and_exceptions(self):
        utc = datetime.timezone.utc
        d = datetime.datetime.now(utc).date()
        f = lambda x: x.strftime("%Y%m%d")                              # noqa: E731
        text = ICS.format(d=f(d), y=f(d - datetime.timedelta(days=10)), x=f(d + datetime.timedelta(days=1)),
                          t=f(d))
        start = datetime.datetime.combine(d, datetime.time(0), tzinfo=utc)
        got = A.ics_events(text, start, start + datetime.timedelta(days=3))
        titles = [e["title"] for e in got]
        self.assertIn("Planning", titles)
        self.assertNotIn("Public holiday", titles)            # all-day events are left out
        self.assertNotIn("Cancelled", titles)
        # today's standup was moved, tomorrow's is excluded, the day after tomorrow's is a plain occurrence
        self.assertEqual(titles.count("Standup"), 1)
        self.assertIn("Standup (moved)", titles)
        self.assertIn("Berlin sync with a very long folded title", titles)
        plan = next(e for e in got if e["title"] == "Planning")
        self.assertEqual(plan["url"], "https://meet.google.com/abc-defg-hij")
        self.assertEqual(plan["end"] - plan["start"], 1800)
        berlin = next(e for e in got if e["title"].startswith("Berlin"))
        import zoneinfo
        want = datetime.datetime.combine(d, datetime.time(18), tzinfo=zoneinfo.ZoneInfo("Europe/Berlin"))
        self.assertEqual(berlin["start"], want.timestamp())

    def test_calendar_url_parsing(self):
        urls = A.SERVICES["calendar"].urls({"urls": "webcal://x.test/a.ics\n\n https://y.test/b.ics "})
        self.assertEqual(urls, ["https://x.test/a.ics", "https://y.test/b.ics"])


class StoreTests(unittest.TestCase):
    def test_details_file_is_private_and_sync_survives_failures(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            A.CONF, A.CACHE = d + "/c", d + "/k"
            A.ACCOUNTS_FILE, A.CACHE_FILE = A.CONF + "/accounts.json", A.CACHE + "/accounts.json"
            A.save_accounts({"todoist": {"token": "t", "_base": "http://127.0.0.1:9"}})
            self.assertEqual(oct(os.stat(A.ACCOUNTS_FILE).st_mode & 0o777), "0o600")
            self.assertEqual(A.connected(), ["todoist"])
            r = A.sync()
            self.assertFalse(r["todoist"]["ok"])
            self.assertIn("Cannot reach", r["todoist"]["error"])
            cache = A.read_cache()["services"]["todoist"]            # a failed sync keeps earlier data
            cache["tasks"] = [{"id": "1"}]
            with open(A.CACHE_FILE, "w") as f:
                json.dump({"services": {"todoist": cache}}, f)
            self.assertEqual(A.sync()["todoist"]["tasks"], [{"id": "1"}])


if __name__ == "__main__":
    unittest.main()
