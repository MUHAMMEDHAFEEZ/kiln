"""Tests for the Lark Base service in share/accounts.py, against a fake `lark-cli` (no network, no account).

    python3 -m unittest discover -s tests
"""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import accounts as A  # noqa: E402

FAKE = r'''#!/usr/bin/python3
import json, os, sys
args = sys.argv[1:]
if os.environ.get("FAKE_FAIL"):
    print(json.dumps({"ok": False, "error": {"type": "authorization", "subtype": os.environ["FAKE_FAIL"], "message": "x"}}))
    sys.exit(1)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"identities": {"user": {"openId": "ou_me", "userName": "Me Myself"}}}))
elif args[:2] == ["base", "+base-get"]:
    print(json.dumps({"ok": True, "data": {"base": {"name": "Task management"}}}))
elif args[:2] == ["base", "+field-list"]:
    print(json.dumps({"ok": True, "data": {"fields": [
        {"name": "Status", "type": "select", "multiple": False, "options": [{"name": "Not yet started"}, {"name": "Ongoing "}, {"name": "Completed"}]},
        {"name": "PROJECTS", "type": "select", "multiple": True, "options": [{"name": "SHOP"}, {"name": "BLOG"}]},
        {"name": "Priority", "type": "select", "multiple": False, "options": [{"name": "Normal"}, {"name": "Important"}]},
        {"name": "Departments", "type": "select", "multiple": True, "options": [{"name": "Backend"}, {"name": "UI/UX"}]},
        {"name": "Task leader", "type": "user", "multiple": True}]}}))
elif args[:2] == ["base", "+record-upsert"]:
    if os.environ.get("FAKE_NO_WRITE"):
        print(json.dumps({"ok": False, "error": {"type": "authorization", "subtype": "missing_scope", "message": "x"}}))
        sys.exit(1)
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps({"json": json.loads(args[args.index("--json") + 1]),
                            "record": args[args.index("--record-id") + 1] if "--record-id" in args else None}) + "\n")
    print(json.dumps({"ok": True, "data": {"record": {"record_id": "recNEW1"}}}))
elif args[:2] == ["base", "+record-upload-attachment"]:
    if os.environ.get("FAKE_NO_UPLOAD"):
        print(json.dumps({"ok": False, "error": {"type": "authorization", "subtype": "missing_scope",
                                                 "message": "missing required scope(s): docs:document.media:upload"}}))
        sys.exit(1)
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps({"upload": args}) + "\n")
    print(json.dumps({"ok": True, "data": {}}))
elif args[:2] == ["base", "+record-list"]:
    offset = int(args[args.index("--offset") + 1]) if "--offset" in args else 0
    fields = ["Task", "Status", "Task leader", "PROJECTS", "Priority", "Estimate Deadline", "Departments"]
    me, other = [{"id": "ou_me", "name": "Me Myself"}], [{"id": "ou_x", "name": "Omar Ghareeb"}]
    rows = [
        ["Ship login", ["Ongoing "], me, ["SHOP"], ["Important"], "2030-01-02T00:00:00.000+02:00", ["Backend"]],
        ["Write docs", ["Not yet started"], other, ["SHOP"], None, None, ["UI/UX"]],
        ["Old thing", ["Completed"], me, ["SHOP"], None, None, None],
        ["Pricing page", ["Not yet started"], other, ["BLOG"], None, None, None],
        ["Unrelated", ["Ongoing"], other, ["DOCS"], None, None, None],
    ]
    page = rows[:3] if offset == 0 else rows[3:]
    ids = [f"rec{offset + i}" for i in range(len(page))]
    print(json.dumps({"ok": True, "data": {"fields": fields, "data": page, "record_id_list": ids,
                                           "has_more": offset == 0}}))
'''
LINK = "https://example.larksuite.com/base/BascExampleToken1?table=tblExampleTable1&view=vewExampleView1"


class LarkBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        exe = f"{self.tmp.name}/lark-cli"
        with open(exe, "w") as f:
            f.write(FAKE)
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = self.tmp.name + os.pathsep + self.old_path
        os.environ.pop("FAKE_FAIL", None)
        os.environ.pop("FAKE_NO_WRITE", None)
        os.environ.pop("FAKE_NO_UPLOAD", None)
        self.log = f"{self.tmp.name}/writes.jsonl"
        os.environ["FAKE_LOG"] = self.log
        self.svc = A.SERVICES["larkbase"]

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        os.environ.pop("FAKE_FAIL", None)
        os.environ.pop("FAKE_NO_WRITE", None)
        os.environ.pop("FAKE_NO_UPLOAD", None)
        self.tmp.cleanup()

    def test_link_parsing(self):
        self.assertEqual(self.svc.parse_link(LINK)[:2], ("BascExampleToken1", "tblExampleTable1"))
        self.assertEqual(self.svc.parse_link(LINK)[2], "https://example.larksuite.com/base/BascExampleToken1")
        for bad in ("", "https://example.com", "https://x.larksuite.com/base/abc", "https://x.larksuite.com/docx/abc?table=tbl1"):
            with self.assertRaises(A.ServiceError):
                self.svc.parse_link(bad)

    def test_lists_are_parsed(self):
        got = self.svc.parse_lists("SHOP = PROJECTS: SHOP\n\nBoth = PROJECTS: BLOG, DOCS\nbad line\nMine = Task leader: me")
        self.assertEqual(got, [("SHOP", "PROJECTS", ["shop"]), ("Both", "PROJECTS", ["blog", "docs"]),
                               ("Mine", "Task leader", ["me"])])

    def test_check_names_the_base(self):
        self.assertEqual(self.svc.check({"link": LINK}), "Task management")

    def test_fetch_builds_lists_pages_and_marks_what_belongs_in_today(self):
        cfg = {"link": LINK, "lists": "SHOP = PROJECTS: SHOP\nBLOG = PROJECTS: BLOG\nAda = Task leader: me"}
        d = self.svc.fetch(cfg)
        self.assertEqual(d["lists"], ["SHOP", "BLOG", "Ada"])
        self.assertEqual(d["me"], "Me Myself")
        by = {t["title"]: t for t in d["tasks"]}
        self.assertEqual(len(by), 4)                                   # both pages read, the unrelated row left out
        self.assertEqual(by["Ship login"]["groups"], ["SHOP", "Ada"])
        self.assertEqual(by["Pricing page"]["groups"], ["BLOG"])
        self.assertTrue(by["Old thing"]["done"])
        self.assertEqual(by["Ship login"]["status"], "Ongoing")        # the stray space is gone
        self.assertEqual(by["Ship login"]["priority"], "Important")
        self.assertEqual(by["Ship login"]["detail"], "Backend")
        self.assertEqual(by["Write docs"]["owners"], ["Omar Ghareeb"])
        self.assertTrue(by["Ship login"]["due"])
        self.assertIn("record=rec0", by["Ship login"]["url"])
        self.assertEqual([t["title"] for t in d["tasks"] if t["today"]], ["Ship login"])   # mine and ongoing only

    def test_without_lists_you_get_yours_and_one_per_project(self):
        d = self.svc.fetch({"link": LINK})
        self.assertEqual(d["lists"], ["Mine", "SHOP", "BLOG", "DOCS"])

    def test_missing_permission_tells_what_to_run(self):
        os.environ["FAKE_FAIL"] = "missing_scope"
        with self.assertRaises(A.ServiceError) as c:
            self.svc.check({"link": LINK})
        self.assertIn("lark-cli auth login --scope", str(c.exception))

    def writes(self):
        with open(self.log) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_options_keep_the_exact_names(self):
        o = self.svc.options({"link": LINK})
        self.assertEqual(o["status"], ["Not yet started", "Ongoing ", "Completed"])      # note the space in "Ongoing "
        self.assertEqual(o["project"], ["SHOP", "BLOG"])

    def test_create_uses_exact_option_names_and_people_ids(self):
        rid = self.svc.create({"link": LINK}, " Fix the login ", project="shop", status="ongoing", priority="important",
                              owners=["ou_me"], due="2030-01-02", dept=["backend"], note="details")
        self.assertEqual(rid, "recNEW1")
        w = self.writes()[0]
        self.assertIsNone(w["record"])
        self.assertEqual(w["json"], {"Task": "Fix the login", "Status": ["Ongoing "], "Priority": ["Important"],
                                     "PROJECTS": ["SHOP"], "Departments": ["Backend"], "Task leader": [{"id": "ou_me"}],
                                     "Estimate Deadline": "2030-01-02 00:00", "description": "details"})

    def test_create_refuses_nonsense_before_writing(self):
        with self.assertRaises(A.ServiceError):
            self.svc.create({"link": LINK}, "  ")
        with self.assertRaises(A.ServiceError) as c:
            self.svc.create({"link": LINK}, "x", project="Nope")
        self.assertIn("Nope", str(c.exception))
        self.assertFalse(os.path.exists(self.log))

    def test_moving_a_task_and_completing_it(self):
        self.svc.set_status({"link": LINK}, "rec7", "Ongoing")
        self.svc.complete({"link": LINK}, "rec8")
        a, b = self.writes()
        self.assertEqual((a["record"], a["json"]), ("rec7", {"Status": ["Ongoing "]}))
        self.assertEqual((b["record"], b["json"]), ("rec8", {"Status": ["Completed"]}))
        with self.assertRaises(A.ServiceError):
            self.svc.set_status({"link": LINK}, "rec7", "Someday")

    def test_a_missing_write_permission_says_how_to_fix_it(self):
        os.environ["FAKE_NO_WRITE"] = "1"
        with self.assertRaises(A.ServiceError) as c:
            self.svc.set_status({"link": LINK}, "rec7", "Ongoing")
        self.assertIn("base:record:create base:record:update", str(c.exception))

    # ---- pictures and files on a new task
    def read_log(self):
        with open(self.log) as f:
            return f.read()

    def make_files(self, *names):
        paths = []
        for n in names:
            p = os.path.join(self.tmp.name, n)
            with open(p, "wb") as f:
                f.write(b"x")
            paths.append(p)
        return paths

    def test_pictures_go_to_images_and_other_files_to_docs(self):
        shot, pdf, shot2 = self.make_files("shot.PNG", "spec.pdf", "b.jpg")
        self.assertEqual(self.svc.attach({"link": LINK}, "recNEW1", [shot, pdf, shot2, shot]), 3)   # a repeat counts once
        ups = [json.loads(l)["upload"] for l in self.read_log().splitlines()]
        self.assertEqual(len(ups), 2)
        self.assertEqual(ups[0][ups[0].index("--field-id") + 1], "Images")
        self.assertEqual([ups[0][i + 1] for i, a in enumerate(ups[0]) if a == "--file"], [shot, shot2])
        self.assertEqual(ups[1][ups[1].index("--field-id") + 1], "DOCS")
        self.assertEqual(ups[1][ups[1].index("--record-id") + 1], "recNEW1")

    def test_the_column_names_can_be_changed(self):
        (shot,) = self.make_files("a.png")
        self.svc.attach({"link": LINK, "images_field": "Screenshots"}, "rec1", [shot])
        up = json.loads(self.read_log())["upload"]
        self.assertEqual(up[up.index("--field-id") + 1], "Screenshots")

    def test_a_missing_file_or_too_many_is_refused_before_anything_is_sent(self):
        with self.assertRaises(A.ServiceError):
            self.svc.attach({"link": LINK}, "rec1", [os.path.join(self.tmp.name, "nope.png")])
        many = self.make_files(*[f"f{i}.png" for i in range(A.LarkBase.MAX_FILES + 1)])
        with self.assertRaises(A.ServiceError):
            self.svc.attach({"link": LINK}, "rec1", many)
        self.assertFalse(os.path.exists(self.log))

    def test_a_missing_upload_permission_says_how_to_fix_it(self):
        (shot,) = self.make_files("a.png")
        os.environ["FAKE_NO_UPLOAD"] = "1"
        with self.assertRaises(A.ServiceError) as c:
            self.svc.attach({"link": LINK}, "rec1", [shot])
        self.assertIn("docs:document.media:upload", str(c.exception))

    def test_the_task_is_kept_when_only_the_upload_fails(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
        import newtask
        old_sync, old_remember = A.sync, A.remember_created
        A.sync, A.remember_created = lambda *_a, **_k: None, lambda *_a: None
        try:
            (shot,) = self.make_files("a.png")
            self.assertEqual(newtask.create_and_sync(self.svc, {"link": LINK}, title="With picture", files=[shot]), "recNEW1")
            os.environ["FAKE_NO_UPLOAD"] = "1"
            with self.assertRaises(newtask.PartialError) as c:
                newtask.create_and_sync(self.svc, {"link": LINK}, title="Another", files=[shot])
            self.assertEqual(c.exception.rid, "recNEW1")              # the task exists: it is not added twice
            self.assertIn("The task was added", str(c.exception))
            self.assertEqual(len([w for w in self.writes() if "json" in w]), 2)
        finally:
            A.sync, A.remember_created = old_sync, old_remember


class NewTaskTests(unittest.TestCase):
    def test_a_tab_name_becomes_the_project_to_preselect(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
        import newtask
        cfg = {"link": LINK, "lists": "SHOP = PROJECTS: SHOP\nBLOG = PROJECTS: BLOG\nAda = Task leader: me"}
        self.assertEqual(newtask.list_to_project(cfg, "SHOP"), "shop")
        self.assertEqual(newtask.list_to_project(cfg, "blog"), "blog")
        self.assertIsNone(newtask.list_to_project(cfg, "Ada"))        # "mine" is not a project
        self.assertIsNone(newtask.list_to_project(cfg, "All"))
        self.assertIsNone(newtask.list_to_project(cfg, None))
        self.assertEqual(newtask.list_to_project({"link": LINK}, "DOCS"), "DOCS")   # default lists are per project


if __name__ == "__main__":
    unittest.main()


class LinkCellTests(unittest.TestCase):
    def test_markdown_links(self):
        L = A.LarkBase
        self.assertEqual(L.links("[Docs](https://a.test/x)"), [("Docs", "https://a.test/x")])
        self.assertEqual(L.links("[Swagger Link](http://Swagger Link)"), [("Swagger Link", "")])   # not a real address
        self.assertEqual(L.links("[x](javascript:alert(1))"), [("x", "")])                          # only web links open
        self.assertEqual(L.links(None), [])
