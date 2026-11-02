#!/usr/bin/python3
"""A standup note from your Lark Base board: what you finished, what you are on, what is next, what needs attention.

    standup.py            print it
    standup.py --window   show it in a window with a Copy button (what the board's Standup button opens)

"Done" comes from the changes Kiln noticed between its refreshes (a task that turned Completed), so it fills up as Kiln
runs; Lark Base itself does not keep a completion date.
"""
import datetime
import os
import sys
import time

SHARE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SHARE)
import accounts as A  # noqa: E402
import timelog as TL  # noqa: E402

KIND = A.LarkBase.kind


def first(s):
    return A.clean_text((s or "").split(",")[0], 40)           # (everything printed here comes from a shared board)


def line(t, note=False, spent=None):
    tag = " · ".join(x for x in (first(t.get("project")), first(t.get("detail"))) if x)
    out = "• " + A.clean_text(t["title"], 120) + (f" ({tag})" if tag else "") + (f" · {TL.fmt(spent)}" if spent else "")
    text = A.clean_text(((t.get("note") or "").strip().splitlines() or [""])[0], 120)
    if note and text:
        out += " — " + (text[:100] + "…" if len(text) > 100 else text)
    return out


def rank(t):
    return (0 if (t.get("priority") or "").lower() == "important" else 1, t.get("due") or 9e12, t["title"].lower())


def build(entry, now=None):
    """The note as text, from the cache entry of the Lark Base service."""
    now = now or time.time()
    me = entry.get("me") or ""
    tasks = [t for t in entry.get("tasks") or [] if me and me in (t.get("owners") or [])]
    today = datetime.date.fromtimestamp(now)
    back = 3 if today.weekday() == 0 else 1                      # on Monday "yesterday" is Friday
    since = datetime.datetime.combine(today - datetime.timedelta(days=back), datetime.time()).timestamp()
    worked = TL.on_day(today, now)                                 # seconds per task today (running one included)
    total = TL.totals(now)
    seen, done = set(), []
    for c in reversed(entry.get("changes") or []):
        if c.get("kind") == "status" and KIND(c.get("to")) == "done" and c.get("at", 0) >= since and me in c.get("owners", []) \
                and c["id"] not in seen:
            seen.add(c["id"])
            done.append(dict(title=c["title"], project=c.get("project", ""), id=c["id"]))
    doing = sorted((t for t in tasks if KIND(t.get("status")) == "doing"), key=rank)
    nxt = sorted((t for t in tasks if KIND(t.get("status")) == "todo"), key=rank)[:3]
    late = [t for t in sorted(tasks, key=rank) if KIND(t.get("status")) != "done" and t.get("due") and t["due"] < now
            and (t.get("priority") or "").lower() == "important"][:3]
    out = [f"Standup · {today.strftime('%a %-d %b')}", "", "Done" + (" since Friday" if back == 3 else " since yesterday")]
    out += [line(t, spent=total.get(t.get("id"))) for t in done] or \
        ["• nothing recorded yet (Kiln notes tasks that turn Completed while it runs)"]
    out += ["", "Today"] + ([line(t, note=True, spent=worked.get(t["id"])) for t in doing] or ["• nothing marked Ongoing"])
    if nxt:
        out += ["", "Next"] + [line(t) for t in nxt]
    if late:
        out += ["", "Needs attention (important, past its date)"] + [line(t) for t in late]
    if worked:
        out += ["", f"Time tracked today: {TL.fmt(sum(worked.values()))}"]
    return "\n".join(out) + "\n"


def entry():
    if "larkbase" not in A.connected():
        raise SystemExit("Connect a Lark Base first (Kiln Settings → Accounts).")
    return A.read_cache().get("services", {}).get("larkbase") or {}


def window(text):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk
    import theme as T
    win = Gtk.Window(title="Standup")
    win.set_default_size(520, 420)
    win.set_icon_name("kiln")
    win.set_position(Gtk.WindowPosition.CENTER)
    try:
        cur = open(T.CURRENT, encoding="utf-8").read().strip()
        prov = Gtk.CssProvider()
        prov.load_from_data(T.gtk_app_css(T.load_themes()["themes"][cur]["colors"]).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    except (OSError, KeyError, ValueError):
        pass
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    for m in ("start", "end", "top", "bottom"):
        getattr(box, f"set_margin_{m}")(16)
    win.add(box)
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
    buf = view.get_buffer()
    # an invisible left-to-right mark in front of every line keeps lines that contain Arabic aligned left;
    # it is taken out again when you copy
    buf.set_text("\n".join("\u200e" + ln if ln else ln for ln in text.split("\n")))
    frame = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)
    frame.add(view)
    box.pack_start(frame, True, True, 0)
    row = Gtk.Box(spacing=8)
    msg = Gtk.Label(label="You can edit it before copying.", xalign=0)
    msg.set_opacity(0.65)
    row.pack_start(msg, True, True, 0)
    copy = Gtk.Button(label="Copy")
    copy.get_style_context().add_class("suggested-action")

    def do_copy(*_):
        buf = view.get_buffer()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(
            buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).replace("\u200e", ""), -1)
        msg.set_text("Copied. Paste it in your chat.")
    copy.connect("clicked", do_copy)
    row.pack_end(copy, False, False, 0)
    box.pack_start(row, False, False, 0)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


def main(argv):
    text = build(entry())
    if "--window" in argv:
        window(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
