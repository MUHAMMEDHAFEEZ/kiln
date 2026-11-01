#!/usr/bin/python3
"""Add a task to the Lark Base table connected in Kiln (Settings → Accounts).

    newtask.py                          open the "New task" window
    newtask.py --list SHOP               ... with the project of that tab chosen
    newtask.py --title "Fix login" --list SHOP --priority Important --due 2026-12-01 --yes
                                        add it without a window (also: --status, --dept, --note, --me / --nobody)

Needs write permission once:  lark-cli auth login --scope "base:record:create base:record:update"
"""
import argparse
import datetime
import os
import sys
import threading

HOME = os.path.expanduser("~")
SHARE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SHARE)
import accounts as A  # noqa: E402

NONE = "—"


def context():
    """(service, its saved details, the cache entry with the people and lists it reported)"""
    if "larkbase" not in A.connected():
        raise A.ServiceError("Connect a Lark Base first (Kiln Settings → Accounts).")
    return A.SERVICES["larkbase"], A.details("larkbase"), A.read_cache().get("services", {}).get("larkbase") or {}


def list_to_project(cfg, name):
    """The project value behind a tab name, from the lists in the settings ('SHOP = PROJECTS: SHOP')."""
    svc = A.SERVICES["larkbase"]
    field = svc._fieldmap(cfg)["project"]
    configured = svc.parse_lists(cfg.get("lists"))
    for lname, f, wanted in configured:
        if lname.lower() == (name or "").lower():
            return wanted[0] if f == field and wanted and wanted[0] != "me" else None   # a person's list is no project
    if configured:                                     # a name the lists do not know: nothing to preselect
        return None
    return name if name and name.lower() not in ("mine", "me", "all") else None       # default lists: one per project


def create_and_sync(svc, cfg, **kw):
    rid = svc.create(cfg, **kw)
    if rid:
        A.remember_created(rid)
    A.sync("larkbase")
    return rid


def cli(args):
    try:
        svc, cfg, cache = context()
        owners = [] if args.nobody else [cache.get("me_id")] if cache.get("me_id") else []
        create_and_sync(svc, cfg, title=args.title, project=list_to_project(cfg, args.list), status=args.status,
                        priority=args.priority, owners=owners, due=args.due, dept=args.dept, note=args.note)
        print("Added.")
        return 0
    except A.ServiceError as e:
        print(e, file=sys.stderr)
        return 1


def gui(args):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    import theme as T

    try:
        svc, cfg, cache = context()
    except A.ServiceError as e:
        d = Gtk.MessageDialog(text="Cannot add a task", secondary_text=str(e), buttons=Gtk.ButtonsType.CLOSE)
        d.run()
        return 1

    win = Gtk.Window(title="New task")
    win.set_default_size(520, -1)
    win.set_icon_name("kiln")
    win.set_position(Gtk.WindowPosition.CENTER)
    try:
        cur = open(T.CURRENT, encoding="utf-8").read().strip()
        colors = T.load_themes()["themes"][cur]["colors"]
        prov = Gtk.CssProvider()
        prov.load_from_data(T.gtk_app_css(colors).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    except (OSError, KeyError, ValueError):
        pass

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for m in ("start", "end", "top", "bottom"):
        getattr(box, f"set_margin_{m}")(20)
    win.add(box)

    def label(text, dim=False):
        lb = Gtk.Label(label=text, xalign=0)
        if dim:
            lb.set_opacity(0.65)
        return lb

    def combo(items, active=None):
        c = Gtk.ComboBoxText()
        for it in items:
            c.append(it, it)
        c.set_active_id(active if active in items else items[0])
        return c

    box.pack_start(label("Title"), False, False, 0)
    title = Gtk.Entry(placeholder_text="What needs doing?", activates_default=True)
    box.pack_start(title, False, False, 0)

    grid = Gtk.Grid(column_spacing=14, row_spacing=6, column_homogeneous=True)
    box.pack_start(grid, False, False, 4)
    widgets = {}
    people = {i: n for i, n in (cache.get("people") or {}).items()}
    me_id = cache.get("me_id") or ""
    for col, (key, name) in enumerate((("project", "Project"), ("status", "Status"))):
        grid.attach(label(name), col, 0, 1, 1)
        widgets[key] = combo([NONE, "Loading…"] if key == "project" else ["Loading…"])
        grid.attach(widgets[key], col, 1, 1, 1)
    for col, (key, name) in enumerate((("priority", "Priority"), ("dept", "Department"))):
        grid.attach(label(name), col, 2, 1, 1)
        widgets[key] = combo([NONE, "Loading…"])
        grid.attach(widgets[key], col, 3, 1, 1)
    grid.attach(label("For"), 0, 4, 1, 1)
    who = ["Me" + (f" ({cache['me']})" if cache.get("me") else "")] + \
        [n for i, n in sorted(people.items(), key=lambda kv: kv[1]) if i != me_id] + ["Nobody yet"]
    widgets["owner"] = combo(who, who[0])
    grid.attach(widgets["owner"], 0, 5, 1, 1)
    grid.attach(label("Due date"), 1, 4, 1, 1)
    due_row = Gtk.Box(spacing=4)
    due = Gtk.Entry(placeholder_text="YYYY-MM-DD", width_chars=11)
    due_row.pack_start(due, True, True, 0)
    for lb, days in (("Today", 0), ("Tomorrow", 1), ("+1 w", 7)):
        b = Gtk.Button(label=lb)
        b.connect("clicked", lambda _b, d=days: due.set_text((datetime.date.today() + datetime.timedelta(days=d)).isoformat()))
        due_row.pack_start(b, False, False, 0)
    grid.attach(due_row, 1, 5, 1, 1)

    box.pack_start(label("Note (optional)"), False, False, 0)
    note = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD, top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
    frame = Gtk.ScrolledWindow(min_content_height=70, shadow_type=Gtk.ShadowType.IN)
    frame.add(note)
    box.pack_start(frame, True, True, 0)
    msg = label("")
    msg.set_line_wrap(True)
    box.pack_start(msg, False, False, 4)
    buttons = Gtk.Box(spacing=8)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: win.destroy())
    add = Gtk.Button(label="Add task")
    add.get_style_context().add_class("suggested-action")
    add.set_sensitive(False)
    add.set_can_default(True)
    win.set_default(add)
    buttons.pack_end(add, False, False, 0)
    buttons.pack_end(cancel, False, False, 0)
    box.pack_start(buttons, False, False, 0)

    state = {"opts": None}

    def fill(opts, err=None):
        if err:
            msg.set_text(err)
            return False
        state["opts"] = opts
        pre = list_to_project(cfg, args.list)
        for key, items, first in (("project", opts["project"], NONE), ("priority", opts["priority"], NONE),
                                  ("dept", opts["dept"], NONE), ("status", opts["status"], None)):
            c = widgets[key]
            c.remove_all()
            names = ([first] if first else []) + [o.strip() for o in items]
            for n in names:
                c.append(n, n)
            wanted = {"project": (pre or "").strip(), "priority": "Normal", "status": "Not yet started"}.get(key, "")
            ids = names
            c.set_active_id(next((n for n in ids if n.lower() == wanted.lower()), names[0]))
        add.set_sensitive(True)
        return False

    def load():
        try:
            opts = svc.options(cfg)
            GLib.idle_add(fill, opts)
        except A.ServiceError as e:
            GLib.idle_add(fill, None, str(e))
    threading.Thread(target=load, daemon=True).start()

    def finished(err):
        add.set_sensitive(True)
        if err:
            msg.set_text(err)
            return False
        win.destroy()
        return False

    def on_add(*_):
        text = title.get_text().strip()
        if not text:
            msg.set_text("Give the task a title.")
            return
        due_text = due.get_text().strip()
        if due_text:
            try:
                datetime.date.fromisoformat(due_text)
            except ValueError:
                msg.set_text("The date should look like 2026-12-31.")
                return
        pick = lambda k: None if widgets[k].get_active_id() in (None, NONE, "Loading…") else widgets[k].get_active_id()  # noqa: E731
        who_name = widgets["owner"].get_active_id()
        owners = [me_id] if who_name == who[0] and me_id else \
            [i for i, n in people.items() if n == who_name]
        buf = note.get_buffer()
        kw = dict(title=text, project=pick("project"), status=pick("status"), priority=pick("priority"), owners=owners,
                  due=due_text or None, dept=pick("dept"), note=buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip() or None,
                  opts=state["opts"])
        add.set_sensitive(False)
        msg.set_text("Adding…")

        def work():
            try:
                create_and_sync(svc, cfg, **kw)
                GLib.idle_add(finished, None)
            except A.ServiceError as e:
                GLib.idle_add(finished, str(e))
        threading.Thread(target=work, daemon=True).start()
    add.connect("clicked", on_add)
    title.connect("activate", on_add)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    title.grab_focus()
    Gtk.main()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Add a task to the Lark Base connected in Kiln.")
    ap.add_argument("--list", help="a tab of the board (its project is chosen for you)")
    ap.add_argument("--title")
    ap.add_argument("--status")
    ap.add_argument("--priority")
    ap.add_argument("--due", help="YYYY-MM-DD")
    ap.add_argument("--dept")
    ap.add_argument("--note")
    ap.add_argument("--nobody", action="store_true", help="do not assign it to you")
    ap.add_argument("--yes", action="store_true", help="add it now, without a window")
    args = ap.parse_args()
    if args.yes or args.title:
        if not args.title:
            ap.error("--yes needs --title")
        return cli(args)
    return gui(args)


if __name__ == "__main__":
    sys.exit(main())
