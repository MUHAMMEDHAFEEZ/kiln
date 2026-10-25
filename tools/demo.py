#!/usr/bin/env python3
"""Kiln demo: run the desktop on made-up data and take README screenshots from it.

Nothing of your own shows up: the projects, tasks, meetings, servers and the terminal all come from
a throwaway folder (default /tmp/kiln-demo) and a small fake server that plays Todoist, Linear,
Trello and a calendar. Needs a running X11 session, xdotool, ImageMagick (`import`) and Pillow.

    python3 tools/demo.py start            build the folder, stop your Kiln, start the demo
    python3 tools/demo.py shoot out.png    desktop screenshot (the Kiln windows over the wallpaper)
    python3 tools/demo.py settings out.png open Kiln Settings on Accounts and capture it
    python3 tools/demo.py gif out.gif      Overview -> Coding -> Focus -> Overview
    python3 tools/demo.py stop             stop the demo, start your own Kiln again
"""
import datetime
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.environ.get("KILN_DEMO_DIR", "/tmp/kiln-demo")
HOME = f"{DEMO}/home"
CONF, CACHE = f"{HOME}/.config/kiln", f"{HOME}/.cache/kiln"
PORT = 8765
ENV = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":1"), HOME=HOME, GDK_BACKEND="x11",
           KILN_DEMO=f"{DEMO}/demo.json", XDG_CONFIG_HOME=f"{HOME}/.config", XDG_CACHE_HOME=f"{HOME}/.cache")
for k in ("KILN_CONFIG", "KILN_CACHE"):
    ENV.pop(k, None)
if "XAUTHORITY" not in ENV and os.path.exists("/run/user/1000/gdm/Xauthority"):
    ENV["XAUTHORITY"] = "/run/user/1000/gdm/Xauthority"

REPOS = {   # name: (language marker file, subtitle, colour, branch)
    "shop-api": ("tsconfig.json", "Storefront backend", "#5b8cff", "feature/checkout-redirect"),
    "mobile-app": ("pubspec.yaml", "iOS and Android client", "#2bc8c0", "main"),
    "docs-site": ("package.json", "Public documentation", "#f2c94c", "main"),
    "data-pipeline": ("requirements.txt", "Nightly reports", "#4b8bff", "fix/late-orders"),
    "edge-proxy": ("go.mod", "Rate limiter", "#7ad1e6", "main"),
}
COMMITS = [   # (hours ago, message)
    (1.5, "Redirect to the order page after checkout"), (4, "Add retry to payment webhook"),
    (22, "Cache the product list for 60 seconds"), (30, "Fix rounding of discounted totals"),
    (50, "Split cart and checkout modules"), (75, "Add order status endpoint"),
    (100, "Upgrade dependencies"), (130, "Initial project setup")]


def sh(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ----------------------------------------------------------------- the made-up world
def build():
    shutil.rmtree(DEMO, ignore_errors=True)
    for d in (CONF, CACHE, f"{CONF}/notes", f"{CONF}/project-todos", f"{HOME}/Desktop", f"{HOME}/Projects"):
        os.makedirs(d)
    now = time.time()
    git_env = dict(os.environ, GIT_AUTHOR_NAME="Ada Demo", GIT_AUTHOR_EMAIL="ada@example.com",
                   GIT_COMMITTER_NAME="Ada Demo", GIT_COMMITTER_EMAIL="ada@example.com")
    for name, (marker, _sub, _col, branch) in REPOS.items():
        p = f"{HOME}/Projects/{name}"
        os.makedirs(f"{p}/src")
        pkg = {"name": name, "scripts": {"dev": "node src/server.js", "build": "tsc", "lint": "eslint .",
                                         "test": "echo 24 tests passed"}}
        files = {"README.md": f"# {name}\n", "src/index.txt": "v1\n", "src/cart.ts": "export const cart = []\n",
                 "src/checkout.ts": "export const checkout = () => {}\n"}
        if marker == "package.json" or name == "shop-api":
            files["package.json"] = json.dumps(pkg, indent=2)
        files[marker] = files.get(marker, "")
        for f, body in files.items():
            os.makedirs(os.path.dirname(f"{p}/{f}"), exist_ok=True)
            open(f"{p}/{f}", "w").write(body)
        sh("git", "-C", p, "init", "-q", "-b", "main")
        n = len(COMMITS) if name == "shop-api" else 3
        for i, (hrs, msg) in enumerate(reversed(COMMITS[:n])):
            open(f"{p}/src/index.txt", "w").write(f"v{i + 1}\n")
            when = datetime.datetime.fromtimestamp(now - hrs * 3600).astimezone().isoformat()
            e = dict(git_env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
            sh("git", "-C", p, "add", "-A", env=e)
            sh("git", "-C", p, "commit", "-q", "-m", msg, env=e)
        if branch != "main":
            sh("git", "-C", p, "checkout", "-q", "-b", branch)
    shop = f"{HOME}/Projects/shop-api"
    open(f"{shop}/src/checkout.ts", "a").write("// redirect\n")
    open(f"{shop}/src/cart.ts", "a").write("// totals\n")
    open(f"{shop}/README.md", "a").write("more\n")
    os.makedirs(f"{shop}/tests")
    open(f"{shop}/tests/redirect.test.ts", "w").write("test()\n")

    for d in ("Projects", "Design", "Invoices", "Reading", "Screenshots", "Archive", "Music"):
        os.makedirs(f"{HOME}/Desktop/{d}")
    cfg = {"desktop_name": "Kiln Desktop", "desktop_dir": f"{HOME}/Desktop", "project_dirs": [f"{HOME}/Projects"],
           "location": {"city": "London", "latitude": 51.5072, "longitude": -0.1276},
           "prayer": {"enabled": True, "method": "mwl", "asr": "standard", "language": "en"},
           "integrations": {"github": True, "lark": False, "claude_code": False}}
    json.dump(cfg, open(f"{CONF}/config.json", "w"), indent=2)
    open(f"{CONF}/current-theme", "w").write("kiln-dark\n")
    open(f"{CONF}/current-project.txt", "w").write(shop + "\n")
    open(f"{CONF}/project-names.txt", "w").write("".join(f"{n} | {s} | {c}\n" for n, (_m, s, c, _b) in REPOS.items()))
    open(f"{CONF}/todo.txt", "w").write("[ ] Book the dentist\n[ ] Reply to the design team\n[x] Back up the photos\n")
    open(f"{CONF}/project-todos/shop-api.txt", "w").write(
        "[ ] Handle expired sessions at checkout\n[ ] Write a test for the redirect\n[x] Add the order page route\n")
    open(f"{CONF}/quick-commands.txt", "w").write("git status\ngit pull\nnpm run dev\ndocker ps\ncode .\n")
    open(f"{CONF}/notes/shop-api.md", "w").write(
        "Checkout redirect\n- send users to /orders/{id}\n- keep the cart until payment is confirmed\n\n"
        "Ask Sam about the webhook retries.\n")
    open(f"{CONF}/notes/_scratch.md", "w").write("Ideas\n- weekly digest email\n- dark mode for the docs\n")
    accounts = {s: {"token": "demo", "_base": f"http://127.0.0.1:{PORT}"} for s in ("todoist", "linear")}
    accounts["trello"] = {"key": "demo", "token": "demo", "_base": f"http://127.0.0.1:{PORT}"}
    accounts["calendar"] = {"urls": f"http://127.0.0.1:{PORT}/calendar.ics"}
    json.dump(accounts, open(f"{CONF}/accounts.json", "w"))
    os.chmod(f"{CONF}/accounts.json", 0o600)

    rnd = random.Random(7)
    weeks = [{"contributionDays": [{"contributionCount": max(0, int(rnd.gauss(3, 3))) if d not in (0, 6)
                                    else max(0, int(rnd.gauss(1, 1.5))), "date": ""} for d in range(7)]}
             for _ in range(53)]
    ago = lambda h: datetime.datetime.fromtimestamp(now - h * 3600, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    gh = {"data": {"viewer": {
        "login": "ada-codes",
        "contributionsCollection": {"contributionCalendar": {"totalContributions": 812, "weeks": weeks}},
        "pullRequests": {"totalCount": 2, "nodes": [
            {"number": 142, "title": "Redirect to the order page after checkout", "url": "https://example.com/pr/142",
             "updatedAt": ago(1.5), "isDraft": False, "repository": {"nameWithOwner": "acme/shop-api"},
             "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}},
            {"number": 138, "title": "Add retry to payment webhook", "url": "https://example.com/pr/138",
             "updatedAt": ago(26), "isDraft": False, "repository": {"nameWithOwner": "acme/shop-api"},
             "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]}}]}},
        "reviews": {"issueCount": 1, "nodes": [
            {"number": 77, "title": "Speed up image resizing", "url": "https://example.com/pr/77",
             "updatedAt": ago(5), "repository": {"nameWithOwner": "acme/docs-site"}, "author": {"login": "sam-dev"}}]}},
        "notif": 3}
    json.dump(gh, open(f"{CACHE}/github.json", "w"))
    open(f"{CACHE}/github.txt", "w").write("user|ada-codes\nnotif|3\nrev|1\nprcount|2\nupdated|%d\n" % now)
    json.dump({"date": time.strftime("%Y-%m-%d"), "sessions": 3, "mode": "focus", "running": False,
               "end_at": now, "left": 1500.0, "length": 1500}, open(f"{CACHE}/focus.json", "w"))
    log = f"{CACHE}/tests-shop-api.log"
    open(log, "w").write("\n".join(["> shop-api@1.0.0 test", "> node --test", "",
                                    "✔ cart adds an item (3ms)", "✔ cart totals with discounts (5ms)",
                                    "✔ checkout creates an order (12ms)", "✔ payment webhook retries (21ms)",
                                    "✔ order status endpoint (4ms)", "", "ℹ tests 24", "ℹ pass 24", "ℹ fail 0"]) + "\n")
    json.dump({shop: {"status": "pass", "t": now - 600, "dur": 6.4, "cmd": "npm test", "log": log}},
              open(f"{CACHE}/tests.json", "w"))
    json.dump({"projects": [f"{HOME}/Projects/{n}" for n in ("shop-api", "mobile-app", "data-pipeline")],
               "servers": [dict(port=3000, label="shop-api · Next.js", pid=1, cwd=shop, kind="server", local=True,
                                proc="node"),
                           dict(port=5432, label="postgres", pid=None, cwd=None, kind="service", proc="postgres")],
               "containers": [dict(name="shop-db", running=True, ports=[5432]),
                              dict(name="redis", running=True, ports=[6379])],
               "top": [["Code", 11], ["node", 7], ["firefox", 5]]}, open(f"{DEMO}/demo.json", "w"))

    # the terminal: Kiln's prompt, with a short made-up session already on screen
    c = json.load(open(f"{ROOT}/share/themes.json"))["themes"]["kiln-dark"]["colors"]
    rgb = lambda h: ";".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))  # noqa: E731
    acc, dim, blue, txt, grn = (rgb(c[k]) for k in ("acc", "dim", "blue", "text", "green"))
    prompt = (f"\\[\\e[38;2;{acc}m\\]demo@kiln\\[\\e[0m\\] \\[\\e[38;2;{dim}m\\]⋮\\[\\e[0m\\] "
              f"\\[\\e[38;2;{blue}m\\]\\w\\[\\e[0m\\]\\n\\[\\e[38;2;{acc}m\\]\\$\\[\\e[0m\\] ")
    head = f"\\e[38;2;{acc}mdemo@kiln\\e[0m \\e[38;2;{dim}m⋮\\e[0m \\e[38;2;{blue}m~/Projects/shop-api\\e[0m"
    cmd = lambda s: f"\\e[38;2;{acc}m$\\e[0m {s}"  # noqa: E731
    lines = [head, cmd("git status -sb"), f"\\e[38;2;{dim}m## feature/checkout-redirect\\e[0m",
             " \\e[38;2;224;180;108mM\\e[0m src/cart.ts", " \\e[38;2;224;180;108mM\\e[0m src/checkout.ts",
             "\\e[38;2;159;184;138m??\\e[0m tests/redirect.test.ts", "", head, cmd("npm test"),
             f"\\e[38;2;{grn}m✔\\e[0m cart totals with discounts \\e[38;2;{dim}m(5ms)\\e[0m",
             f"\\e[38;2;{grn}m✔\\e[0m checkout creates an order \\e[38;2;{dim}m(12ms)\\e[0m",
             f"\\e[38;2;{grn}m✔\\e[0m payment webhook retries \\e[38;2;{dim}m(21ms)\\e[0m",
             f"\\e[38;2;{dim}mℹ tests 24 · pass 24 · fail 0\\e[0m", ""]
    open(f"{CACHE}/term.bashrc", "w").write(
        "cd ~/Projects/shop-api\nPS1='" + prompt + "'\nPROMPT_COMMAND=\n"
        "clear\nprintf '" + "\\n".join(lines) + "\\n'\nunset HISTFILE\n")
    open(f"{HOME}/.bashrc", "w").write("")


# ----------------------------------------------------------------- the fake services
class Fake(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if self.path == "/graphql":
            return self.reply({"data": {"viewer": {"assignedIssues": {"nodes": [
                {"id": "l1", "identifier": "ENG-142", "title": "Fix checkout redirect loop", "url": "https://example.com",
                 "dueDate": None, "state": {"name": "In Progress"}},
                {"id": "l2", "identifier": "ENG-150", "title": "Add rate limit headers", "url": "https://example.com",
                 "dueDate": (datetime.date.today() + datetime.timedelta(days=2)).isoformat(),
                 "state": {"name": "Todo"}}]}}}} if "assignedIssues" in body else {"data": {}})
        self.reply({})

    def do_GET(self):
        now = datetime.datetime.now().astimezone()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.path.startswith("/api/v1/tasks/filter"):
            return self.reply({"results": [
                {"id": "t1", "content": "Review the checkout pull request",
                 "due": {"datetime": (now + datetime.timedelta(hours=1)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}},
                {"id": "t2", "content": "Renew the domain name", "due": {"date": today.date().isoformat()}},
                {"id": "t3", "content": "Send the invoice to Studio North",
                 "due": {"date": (today + datetime.timedelta(days=1)).date().isoformat()}}], "next_cursor": None})
        if self.path.startswith("/1/members/me/boards"):
            return self.reply([{"id": "b1", "name": "Launch plan"}])
        if self.path.startswith("/1/members/me/cards"):
            due = (now + datetime.timedelta(days=1, hours=2)).astimezone(datetime.timezone.utc).isoformat()
            return self.reply([{"id": "c1", "name": "Approve the new pricing page", "due": due, "dueComplete": False,
                                "url": "https://example.com", "idBoard": "b1"}])
        if self.path.startswith("/calendar.ics"):
            def ev(uid, title, start_min, dur, extra=""):
                s = (now + datetime.timedelta(minutes=start_min)).astimezone(datetime.timezone.utc)
                e = s + datetime.timedelta(minutes=dur)
                f = "%Y%m%dT%H%M%SZ"
                return (f"BEGIN:VEVENT\nUID:{uid}\nDTSTART:{s.strftime(f)}\nDTEND:{e.strftime(f)}\n"
                        f"SUMMARY:{title}\n{extra}END:VEVENT\n")
            ics = ("BEGIN:VCALENDAR\nVERSION:2.0\n" +
                   ev("a", "Daily standup", -5, 30, "DESCRIPTION:https://meet.google.com/abc-defg-hij\n") +
                   ev("b", "Design review", 35, 25, "DESCRIPTION:https://zoom.us/j/123456\n") +
                   ev("c", "1:1 with Sam", 70, 20) + "END:VCALENDAR\n")
            return self.reply(ics.encode(), "text/calendar")
        self.reply({})


def serve():
    ThreadingHTTPServer(("127.0.0.1", PORT), Fake).serve_forever()


# ----------------------------------------------------------------- running and capturing
def pids(pattern):
    out = sh("pgrep", "-f", pattern).stdout.split()
    return [int(p) for p in out if int(p) != os.getpid()]


def stop_demo():
    for pat in (f"{ROOT}/share/app.py", "tools/demo.py serve", f"{ROOT}/share/settings.py"):
        for p in pids(re.escape(pat)):
            try:
                os.kill(p, signal.SIGTERM)
            except OSError:
                pass


def start():
    build()
    stop_demo()
    sh("systemctl", "--user", "stop", "kiln")
    time.sleep(1)
    subprocess.Popen([sys.executable, __file__, "serve"], env=ENV, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(1)
    subprocess.run(["/usr/bin/python3", f"{ROOT}/share/accounts.py", "sync"], env=ENV)
    log = open(f"{DEMO}/app.log", "w")
    subprocess.Popen(["/usr/bin/python3", f"{ROOT}/share/app.py"], env=ENV, stdout=log, stderr=log,
                     start_new_session=True)
    time.sleep(10)
    print("demo running; log:", f"{DEMO}/app.log")


def stop():
    stop_demo()
    time.sleep(1)
    subprocess.run([os.path.expanduser("~/.local/share/kiln/start.sh")], env=dict(os.environ, DISPLAY=ENV["DISPLAY"]))
    print("your Kiln is back")


def mode(name):
    with open(f"{CACHE}/cmd", "a") as f:
        f.write(f"mode {name}\n")
    time.sleep(3)


def capture(width=1720):
    from PIL import Image
    geo = sh("xdotool", "getdisplaygeometry", env=ENV).stdout.split()
    w, h = int(geo[0]), int(geo[1])
    wall = Image.open(f"{ROOT}/share/wallpapers/kiln-dark.jpg").convert("RGB").resize((w, h))
    ids = sh("xdotool", "search", "--onlyvisible", "--name", "^Kiln ", env=ENV).stdout.split()
    tmp = f"{DEMO}/_w.png"
    named = sorted(((sh("xdotool", "getwindowname", w, env=ENV).stdout.strip(), w) for w in ids),
                   key=lambda nw: nw[0] != "Kiln Veil")            # the dimming layer goes underneath
    for name, wid in named:
        if name in ("Kiln Settings", "Kiln Search"):
            continue
        g = sh("xdotool", "getwindowgeometry", wid, env=ENV).stdout
        x, y = map(int, re.search(r"Position: (-?\d+),(-?\d+)", g).groups())
        sh("import", "-window", wid, tmp, env=ENV)
        im = Image.open(tmp).convert("RGBA")
        if name == "Kiln Veil":           # the dimming layer: black at 55%, the capture cannot keep its alpha
            im = Image.new("RGBA", im.size, (0, 0, 0, 140))
        if name == "Kiln Icons":          # that window is captured with a black backdrop
            from PIL import ImageChops
            keep = im.convert("L").point(lambda v: 0 if v < 6 else 255)
            im.putalpha(ImageChops.multiply(im.getchannel("A"), keep))
        wall.paste(im, (x, y), im)
    return wall.resize((width, int(width * h / w)), Image.LANCZOS)


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "start":
        start()
    elif cmd == "serve":
        serve()
    elif cmd == "stop":
        stop()
    elif cmd == "shoot" and len(argv) > 1:
        mode(argv[2] if len(argv) > 2 else "overview")
        capture().save(argv[1])
    elif cmd == "settings" and len(argv) > 1:
        proc = subprocess.Popen(["/usr/bin/python3", f"{ROOT}/share/settings.py", "accounts"], env=ENV,
                                start_new_session=True)
        time.sleep(5)
        wid = sh("xdotool", "search", "--all", "--pid", str(proc.pid), "--name", "^Kiln Settings$", env=ENV).stdout.split()[0]
        sh("import", "-window", wid, argv[1], env=ENV)
        proc.terminate()
    elif cmd == "gif" and len(argv) > 1:
        frames = []
        for m in ("overview", "coding", "focus", "overview"):
            mode(m)
            time.sleep(1.5)
            frames.append(capture(1280).convert("P", palette=1, colors=128))
        frames[0].save(argv[1], save_all=True, append_images=frames[1:], duration=[2200, 2200, 2600, 1800], loop=0,
                       optimize=True)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
