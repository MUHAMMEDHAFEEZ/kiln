#!/usr/bin/python3
"""Add a task to the Lark Base table connected in Kiln (Settings → Accounts).

    newtask.py                          open the "New task" window
    newtask.py --list SHOP               ... with the project of that tab chosen
    newtask.py --title "Fix login" --list SHOP --priority Important --due 2026-12-01 --yes
                                        add it without a window (also: --status, --dept, --note, --me / --nobody)
    newtask.py --title "Bug" --image shot.png --image log.pdf --yes
                                        attach pictures (Images column) and other files (DOCS column)

Needs write permission once:  lark-cli auth login --scope "base:record:create base:record:update"
Attachments also need:        lark-cli auth login --scope "docs:document.media:upload"
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


class PartialError(A.ServiceError):
    """The task exists, but its files did not go up (`rid` is the new task, so nobody adds it twice)."""

    def __init__(self, rid, why):
        super().__init__(f"The task was added, but the files did not upload. {why}")
        self.rid = rid


def create_and_sync(svc, cfg, files=None, **kw):
    """Add the task, then upload `files` to it. Raises PartialError when only the upload failed."""
    if files:
        svc.split_files(files)                        # a missing file or too many: say so before anything is created
    rid = svc.create(cfg, **kw)
    if rid:
        A.remember_created(rid)
    failure = None
    if files and rid:
        try:
            svc.attach(cfg, rid, files)
        except A.ServiceError as e:
            failure = e
    A.sync("larkbase")
    if failure:
        raise PartialError(rid, str(failure))
    return rid


def cli(args):
    try:
        svc, cfg, cache = context()
        owners = [] if args.nobody else [cache.get("me_id")] if cache.get("me_id") else []
        create_and_sync(svc, cfg, title=args.title, project=list_to_project(cfg, args.list), status=args.status,
                        priority=args.priority, owners=owners, due=args.due, dept=args.dept, note=args.note,
                        files=args.image)
        print("Added." + (f" ({len(args.image)} file{'s' if len(args.image) != 1 else ''} attached)" if args.image else ""))
        return 0
    except A.ServiceError as e:
        print(e, file=sys.stderr)
        return 1


def attachments_widget(win, colors=None):
    """The "Attachments" part of the window: pictures and other files for the task. Add them with the button, drop them
    on the window, or paste with Ctrl+V (a picture, or files copied in the file manager). Returns (widget, paths(),
    cleanup())."""
    import math
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo
    import keys as KEYS

    colors = colors or {}
    files, pasted = [], []                              # pasted pictures live in the cache until the window closes
    TW, TH = 112, 88

    class Tile(Gtk.DrawingArea):
        """A picture (cropped to fill) or a file badge, with its name and a close button."""

        def __init__(self, path):
            super().__init__()
            self.path, self.hover, self.pix = path, False, None
            self.set_size_request(TW, TH)
            self.set_tooltip_text(path)
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK |
                            Gdk.EventMask.LEAVE_NOTIFY_MASK)
            try:
                self.pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, TW * 3, TH * 3, True)
            except GLib.Error:
                pass
            self.connect("draw", self.on_draw)
            self.connect("motion-notify-event", lambda _w, e: self.set_hover(True, e.x, e.y))
            self.connect("leave-notify-event", lambda *_: self.set_hover(False, 0, 0))
            self.connect("button-press-event", self.on_press)
            self.close_hot = False

        def set_hover(self, on, x, y):
            hot = on and math.hypot(x - (TW - 14), y - 14) <= 11
            if (on, hot) != (self.hover, self.close_hot):
                self.hover, self.close_hot = on, hot
                self.queue_draw()

        def on_press(self, _w, ev):
            if ev.button == 1 and math.hypot(ev.x - (TW - 14), ev.y - 14) <= 12:
                remove(self.path)
            return True

        def on_draw(self, _w, cr):
            def rr(x, y, w, h, r):
                cr.new_sub_path()
                cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
                cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
                cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
                cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
                cr.close_path()
            rr(0.5, 0.5, TW - 1, TH - 1, 12)
            cr.save()
            cr.clip_preserve()
            if self.pix:                                           # cover: fill the tile, crop the rest
                pw, ph = self.pix.get_width(), self.pix.get_height()
                k = max(TW / pw, TH / ph)
                cr.set_source_rgba(1, 1, 1, 0.06)
                cr.paint()
                cr.translate((TW - pw * k) / 2, (TH - ph * k) / 2)
                cr.scale(k, k)
                Gdk.cairo_set_source_pixbuf(cr, self.pix, 0, 0)
                cr.paint()
            else:
                cr.set_source_rgba(1, 1, 1, 0.07)
                cr.paint()
                lay = PangoCairo.create_layout(cr)
                ext = (os.path.splitext(self.path)[1][1:] or "file").upper()[:5]
                lay.set_font_description(Pango.FontDescription("Sans Bold 15"))
                lay.set_text(ext, -1)
                w, h = lay.get_pixel_size()
                cr.set_source_rgba(1, 1, 1, 0.55)
                cr.move_to((TW - w) / 2, (TH - h) / 2 - 8)
                PangoCairo.show_layout(cr, lay)
            cr.restore()
            # name on a dark band
            cr.save()
            rr(0.5, 0.5, TW - 1, TH - 1, 12)
            cr.clip()
            cr.rectangle(0, TH - 24, TW, 24)
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.fill()
            lay = PangoCairo.create_layout(cr)
            lay.set_font_description(Pango.FontDescription("Sans 9"))
            lay.set_width((TW - 14) * Pango.SCALE)
            lay.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lay.set_text(os.path.basename(self.path), -1)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.move_to(7, TH - 20)
            PangoCairo.show_layout(cr, lay)
            cr.restore()
            # border and close button
            rr(0.5, 0.5, TW - 1, TH - 1, 12)
            cr.set_source_rgba(1, 1, 1, 0.14)
            cr.set_line_width(1)
            cr.stroke()
            cr.arc(TW - 14, 14, 10, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, 0.78 if self.close_hot else 0.55)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 1 if self.hover else 0.85)
            cr.set_line_width(1.6)
            for dx, dy in ((-3.2, -3.2), (3.2, -3.2)):
                cr.move_to(TW - 14 + dx, 14 + dy)
                cr.line_to(TW - 14 - dx, 14 - dy)
            cr.stroke()
            return True

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    head = Gtk.Box(spacing=8)
    title = Gtk.Label(label="ATTACHMENTS", xalign=0)
    title.get_style_context().add_class("nt-label")
    head.pack_start(title, True, True, 0)
    count = Gtk.Label(xalign=1)
    count.get_style_context().add_class("nt-label")
    head.pack_start(count, False, False, 4)
    add_btn = Gtk.Button(label="Add files\u2026")
    paste_btn = Gtk.Button(label="Paste")
    paste_btn.set_tooltip_text("Paste a picture or files from the clipboard (Ctrl+V)")
    head.pack_start(add_btn, False, False, 0)
    head.pack_start(paste_btn, False, False, 0)
    root.pack_start(head, False, False, 0)

    zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    zone.get_style_context().add_class("nt-drop")
    stack = Gtk.Stack()
    empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    empty.set_margin_top(16)
    empty.set_margin_bottom(16)
    ico = Gtk.Image.new_from_icon_name("insert-image-symbolic", Gtk.IconSize.DND)
    ico.set_opacity(0.5)
    empty.pack_start(ico, False, False, 0)
    line = Gtk.Label(label="Drop pictures or files here")
    sub = Gtk.Label(label="or press Ctrl+V to paste a screenshot")
    sub.get_style_context().add_class("nt-label")
    empty.pack_start(line, False, False, 0)
    empty.pack_start(sub, False, False, 0)
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12, min_children_per_line=1,
                       column_spacing=10, row_spacing=10, homogeneous=True, halign=Gtk.Align.START)
    flow.set_margin_start(12)
    flow.set_margin_end(12)
    flow.set_margin_top(12)
    flow.set_margin_bottom(12)
    scroller = Gtk.ScrolledWindow(min_content_height=TH + 24, max_content_height=2 * TH + 44, propagate_natural_height=True)
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(flow)
    stack.add_named(empty, "empty")
    stack.add_named(scroller, "files")
    zone.pack_start(stack, True, True, 0)
    root.pack_start(zone, False, False, 0)

    def size_text():
        total = 0
        for f in files:
            try:
                total += os.path.getsize(f)
            except OSError:
                pass
        if not files:
            return ""
        mb = total / 1048576
        return f"{len(files)} file{'s' if len(files) != 1 else ''} \u00b7 " + (f"{mb:.1f} MB" if mb >= 0.1 else f"{max(1, total // 1024)} KB")

    def redraw():
        for c in flow.get_children():
            flow.remove(c)
        for f in files:
            flow.add(Tile(f))
        flow.show_all()
        stack.set_visible_child_name("files" if files else "empty")
        count.set_text(size_text().upper())

    def add(paths):
        for p in paths:
            if not p:
                continue
            p = os.path.abspath(p)
            if os.path.isfile(p) and p not in files and len(files) < A.LarkBase.MAX_FILES:
                files.append(p)
        redraw()

    def remove(path):
        if path in files:
            files.remove(path)
        redraw()

    def choose(*_):
        dlg = Gtk.FileChooserNative(title="Add pictures or files", action=Gtk.FileChooserAction.OPEN,
                                    transient_for=win, select_multiple=True)
        pics = Gtk.FileFilter()
        pics.set_name("Pictures")
        for ext in A.LarkBase.IMAGE_EXT:
            pics.add_pattern("*" + ext)
            pics.add_pattern("*" + ext.upper())
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        dlg.add_filter(pics)
        dlg.add_filter(everything)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            add(dlg.get_filenames())

    def clipboard_paste():
        """True when the clipboard held a picture or files and they were added."""
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if clip.wait_is_uris_available():                    # files copied in the file manager
            paths = [Gio.File.new_for_uri(u).get_path() for u in (clip.wait_for_uris() or []) if u.startswith("file://")]
            if paths:
                add(paths)
                return True
        if clip.wait_is_image_available():
            pix = clip.wait_for_image()
            if pix:
                d = os.path.join(A.CACHE, "attachments")
                os.makedirs(d, mode=0o700, exist_ok=True)
                path = os.path.join(d, f"pasted-{datetime.datetime.now():%Y%m%d-%H%M%S-%f}.png")
                pix.savev(path, "png", [], [])
                pasted.append(path)
                add([path])
                return True
        return False

    add_btn.connect("clicked", choose)
    paste_btn.connect("clicked", lambda *_: clipboard_paste() or count.set_text("NOTHING TO PASTE"))

    def on_key(_w, ev):
        """Ctrl+V: a picture or files on the clipboard are attached, text pastes as usual. It works with any keyboard
        layout (an Arabic layout sends another letter for the V key)."""
        if ev.state & Gdk.ModifierType.CONTROL_MASK and KEYS.latin_key(ev) in (Gdk.KEY_v, Gdk.KEY_V):
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            has_media = clip.wait_is_image_available() or clip.wait_is_uris_available()
            if has_media and (not clip.wait_is_text_available() or isinstance(win.get_focus(), Gtk.Button) or
                              clip.wait_is_uris_available()):
                return clipboard_paste()
            if has_media and clip.wait_is_image_available() and not isinstance(win.get_focus(), (Gtk.Entry, Gtk.TextView)):
                return clipboard_paste()
        return False
    win.connect("key-press-event", on_key)

    win.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
    zone_ctx = zone.get_style_context()
    win.connect("drag-motion", lambda *_: (zone_ctx.add_class("drag"), True)[1])
    win.connect("drag-leave", lambda *_: zone_ctx.remove_class("drag"))

    def dropped(_w, _ctx, _x, _y, data, _info, _time):
        zone_ctx.remove_class("drag")
        add([Gio.File.new_for_uri(u).get_path() for u in data.get_uris() if u.startswith("file://")])
    win.connect("drag-data-received", dropped)

    def cleanup():
        for p in pasted:
            try:
                os.remove(p)
            except OSError:
                pass

    return root, lambda: list(files), cleanup


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
    win.set_default_size(600, -1)
    win.set_icon_name("kiln")
    win.set_position(Gtk.WindowPosition.CENTER)
    colors = {}
    try:
        cur = open(T.CURRENT, encoding="utf-8").read().strip()
        colors = T.load_themes()["themes"][cur]["colors"]
        prov = Gtk.CssProvider()
        prov.load_from_data(T.gtk_app_css(colors).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    except (OSError, KeyError, ValueError):
        pass
    acc = "#" + colors.get("acc", "d97757")
    look = Gtk.CssProvider()
    look.load_from_data(f"""
        .nt-label {{ font-size: 10.5px; font-weight: 700; letter-spacing: 0.8px; opacity: 0.62; }}
        .nt-title {{ font-size: 17px; font-weight: 600; padding: 10px 14px; border-radius: 12px; min-height: 26px; }}
        .nt-note {{ border-radius: 12px; }}
        .nt-note textview {{ font-size: 13.5px; }}
        entry, combobox button.combo {{ border-radius: 10px; min-height: 22px; padding-top: 6px; padding-bottom: 6px; }}
        button {{ border-radius: 10px; min-height: 22px; padding: 6px 14px; }}
        .nt-drop {{ border: 1.5px dashed alpha(currentColor, 0.26); border-radius: 14px; background: alpha(currentColor, 0.035); }}
        .nt-drop.drag {{ border-color: {acc}; background: alpha({acc}, 0.12); }}
        .nt-msg {{ font-size: 12px; opacity: 0.85; }}
    """.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), look, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for m in ("start", "end"):
        getattr(box, f"set_margin_{m}")(26)
    box.set_margin_top(22)
    box.set_margin_bottom(22)
    win.add(box)

    def label(text, dim=False):
        lb = Gtk.Label(label=text.upper(), xalign=0)
        lb.get_style_context().add_class("nt-label")
        lb.set_margin_top(6)
        return lb

    def combo(items, active=None):
        c = Gtk.ComboBoxText()
        for it in items:
            c.append(it, it)
        c.set_active_id(active if active in items else items[0])
        return c

    box.pack_start(label("Title"), False, False, 0)
    title = Gtk.Entry(placeholder_text="What needs doing?", activates_default=True)
    title.get_style_context().add_class("nt-title")
    box.pack_start(title, False, False, 0)

    grid = Gtk.Grid(column_spacing=16, row_spacing=4, column_homogeneous=True)
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
    frame = Gtk.ScrolledWindow(min_content_height=84, shadow_type=Gtk.ShadowType.IN)
    frame.get_style_context().add_class("nt-note")
    frame.add(note)
    box.pack_start(frame, True, True, 0)
    attach_box, attached, attach_cleanup = attachments_widget(win, colors)
    attach_box.set_margin_top(10)
    box.pack_start(attach_box, False, False, 4)
    msg = Gtk.Label(xalign=0)
    msg.get_style_context().add_class("nt-msg")
    msg.set_line_wrap(True)
    box.pack_start(msg, False, False, 4)
    buttons = Gtk.Box(spacing=8)
    buttons.set_margin_top(10)
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

    state = {"opts": None, "rid": None}               # "rid": the task is made, only its files are left

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

    def retry_upload(*_):
        add.set_sensitive(False)
        msg.set_text("Uploading…")

        def work():
            try:
                svc.attach(cfg, state["rid"], attached())
                A.sync("larkbase")
                GLib.idle_add(finished, None)
            except A.ServiceError as e:
                GLib.idle_add(finished, "The files did not upload. " + str(e))
        threading.Thread(target=work, daemon=True).start()

    def on_add(*_):
        if state["rid"]:
            return retry_upload()
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
                  opts=state["opts"], files=attached())
        add.set_sensitive(False)
        msg.set_text("Adding…" if not kw["files"] else "Adding and uploading…")

        def work():
            try:
                create_and_sync(svc, cfg, **kw)
                GLib.idle_add(finished, None)
            except PartialError as e:
                state["rid"] = e.rid
                GLib.idle_add(add.set_label, "Retry upload")
                GLib.idle_add(finished, str(e))
            except A.ServiceError as e:
                GLib.idle_add(finished, str(e))
        threading.Thread(target=work, daemon=True).start()
    add.connect("clicked", on_add)
    title.connect("activate", on_add)
    win.connect("destroy", lambda *_: (attach_cleanup(), Gtk.main_quit()))
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
    ap.add_argument("--image", "--file", action="append", default=[], metavar="PATH",
                    help="attach a picture or file (repeat for more)")
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
