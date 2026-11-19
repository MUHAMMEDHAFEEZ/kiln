#!/usr/bin/python3
"""Your PDF books: the library, where you stopped, bookmarks, highlights, notes and reading statistics.

    books.py [list]        the library, with how far you are in each book
    books.py stats         today, the last 7 days and your streak

The PDFs are plain files in ~/Books (or the folders in `books.dirs`). What you did with them is kept in
~/.config/kiln/books.json: your own data, never sent anywhere. Nothing here needs Poppler; the reader (reader.py) does.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts as A  # noqa: E402  (clean_text: titles and notes end up in files and notifications)

HOME = os.path.expanduser("~")
CONF = os.environ.get("KILN_CONFIG", f"{HOME}/.config/kiln")
CACHE = os.environ.get("KILN_CACHE", f"{HOME}/.cache/kiln")
DATA_FILE = f"{CONF}/books.json"
INDEX_FILE = f"{CACHE}/books-index.json"
COVERS = f"{CACHE}/covers"

THEMES = ("auto", "light", "sepia", "night")
VIEWS = ("single", "spread", "book")                        # one page scrolling, two pages scrolling, an open book with page turns
COVER_MODES = ("auto", "pdf", "designed")                   # auto: the first page of the book, a drawn cover only when that page is blank; pdf: always the first page; designed: a cover drawn by Kiln for every book
COVER_VERSION = 2                                           # changes when the designed covers change, so they are made again
COLORS = ("yellow", "green", "pink", "blue")                 # highlight colours
DEFAULTS = dict(dirs=["~/Books"], theme="auto", daily_goal_minutes=20, eye_break_minutes=30, view="book", covers="auto")
MAX_BOOKS, MAX_RECTS, MAX_TEXT, KEEP_DAYS = 3000, 120, 3000, 400
DAY_MIN_SECS = 60                    # a day counts for the streak from one minute of reading
PAGE_SEEN_SECS = 4                   # a page counts as read after being in view this long
IDLE_SECS = 120                      # no key, click or scroll for this long: the clock stops
FINISHED_AT = 0.97                   # reaching this much of the book marks it finished


def config():
    """The `books` section of config.json with defaults, every value checked."""
    try:
        with open(f"{CONF}/config.json", encoding="utf-8") as f:
            user = json.load(f).get("books") or {}
    except (OSError, ValueError, AttributeError):
        user = {}
    out = dict(DEFAULTS)
    if isinstance(user, dict):
        out.update({k: v for k, v in user.items() if k in DEFAULTS})
    dirs = out["dirs"] if isinstance(out["dirs"], list) else [out["dirs"]]
    out["dirs"] = [os.path.abspath(os.path.expanduser(str(d))) for d in dirs if d] or [f"{HOME}/Books"]
    if out["theme"] not in THEMES:
        out["theme"] = "auto"
    view = user.get("view") if isinstance(user, dict) else None
    if view not in VIEWS:                                    # (an older config had books.spread: true)
        view = "spread" if isinstance(user, dict) and user.get("spread") is True else DEFAULTS["view"]
    out["view"] = view
    if out["covers"] not in COVER_MODES:
        out["covers"] = "auto"
    for key, lo, hi in (("daily_goal_minutes", 1, 600), ("eye_break_minutes", 0, 240)):
        try:
            out[key] = max(lo, min(hi, int(out[key])))
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    return out


# ------------------------------------------------------------------ the files
def book_id(path, size=None):
    """A name for the book that survives moving or renaming the file: its size and its first 64 KB."""
    h = hashlib.sha1()
    try:
        h.update(str(size if size is not None else os.path.getsize(path)).encode())
        with open(path, "rb") as f:
            h.update(f.read(65536))
    except OSError:
        h.update(path.encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def scan(dirs=None, limit=MAX_BOOKS):
    """The PDFs under the books folders: no hidden folders, no folders that are links, each file once."""
    out, seen = [], set()
    for root in dirs or config()["dirs"]:
        for base, names, files in os.walk(root, followlinks=False):
            names[:] = sorted(n for n in names if not n.startswith("."))
            for name in sorted(files):
                if name.startswith(".") or not name.lower().endswith(".pdf"):
                    continue
                path = os.path.join(base, name)
                try:
                    st = os.stat(path)
                    real = os.path.realpath(path)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or real in seen:
                    continue
                seen.add(real)
                out.append(dict(path=path, size=st.st_size, mtime=st.st_mtime))
                if len(out) >= limit:
                    return out
    return out


def cover_file(bid, mode="auto"):
    return f"{COVERS}/{bid}.{mode}.v{COVER_VERSION}.png"


IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"RIFF", ".webp"))
MAX_IMAGE = 20 * 1024 * 1024


def custom_cover(bid):
    """The cover image you chose for a book (kept in ~/.config/kiln/covers), or None."""
    for _magic, ext in IMAGE_MAGIC:
        path = f"{CONF}/covers/{bid}{ext}"
        if os.path.isfile(path):
            return path
    return None


def set_custom_cover(bid, src):
    """Use the image file `src` as the cover of a book. It must really be a PNG, JPEG or WebP picture of a sane size;
    returns the saved path or raises ValueError."""
    try:
        if os.path.getsize(src) > MAX_IMAGE:
            raise ValueError("that picture is too big (over 20 MB)")
        with open(src, "rb") as f:
            head = f.read(16)
    except OSError as exc:
        raise ValueError(f"cannot read it ({exc.strerror})") from exc
    ext = next((e for magic, e in IMAGE_MAGIC if head.startswith(magic) and (e != ".webp" or head[8:12] == b"WEBP")), None)
    if not ext:
        raise ValueError("that is not a PNG, JPEG or WebP picture")
    os.makedirs(f"{CONF}/covers", mode=0o700, exist_ok=True)
    clear_custom_cover(bid)
    dest = f"{CONF}/covers/{bid}{ext}"
    shutil.copyfile(src, dest)
    os.chmod(dest, 0o600)
    return dest


def clear_custom_cover(bid):
    for _magic, ext in IMAGE_MAGIC:
        try:
            os.remove(f"{CONF}/covers/{bid}{ext}")
        except OSError:
            pass


def trash_file(path):
    """Move a file to the Trash (so it can still be brought back). Returns None, or a short reason it did not work.
    A file is never deleted for good here: when the Trash cannot take it, it stays where it is."""
    gio = shutil.which("gio")
    if not gio:
        return "there is no Trash tool (gio) on this system"
    try:
        r = subprocess.run([gio, "trash", "--", path], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "the Trash did not answer"
    if r.returncode != 0:
        return A.clean_text((r.stderr or "").strip().splitlines()[-1] if (r.stderr or "").strip() else "it cannot be trashed", 120)
    return None


def remove_book(store, index, entry):
    """Take a book out of the library: its PDF goes to the Trash, and what Kiln kept about it (position, bookmarks,
    highlights, notes, your cover, the cached cover and title) is forgotten. The reading log (time per day) stays.
    Returns None, or the reason it did not work (then nothing was changed). Only files inside a books folder are touched."""
    path, bid = entry["path"], entry["id"]
    real = os.path.realpath(path)
    if not any(real.startswith(os.path.realpath(d) + os.sep) for d in config()["dirs"]) or not os.path.isfile(real):
        return "that file is not in your books folder"
    err = trash_file(path)
    if err:
        return err
    store.forget(bid)
    store.save()
    clear_custom_cover(bid)
    for f in glob.glob(f"{COVERS}/{bid}.*"):
        try:
            os.remove(f)
        except OSError:
            pass
    index.d.pop(path, None)
    index.save()
    return None


def split_title(title, author=""):
    """('Reinforcement Learning', 'An Introduction', 'Sutton and Barto') from 'Reinforcement Learning - An Introduction
    (Sutton and Barto)': what a cover shows as title, subtitle and author."""
    title = A.clean_text(title, 160)
    m = re.search(r"\s*\(([^()]{2,60})\)\s*$", title)
    if m:
        title, author = title[:m.start()].strip(), author or m.group(1)
    main, sub = (re.split(r"\s+[-–—]\s+|:\s+", title, maxsplit=1) + [""])[:2]
    return main.strip() or title, sub.strip(), A.clean_text(author, 80)


TOPICS = (("ai", ("learning", "neural", "intelligence", "machine", "deep", "reinforcement", "ai ", " ai", "llm", "data science",
                  "mathematics for", "statistics")),
          ("patterns", ("pattern", "architecture", "refactor", "clean code", "idiom", "design")),
          ("web", ("django", "flask", "python", "web", "http", "rest", "api", "javascript", "react", "docs", "documentation")))


def cover_topic(title):
    """What a book is about, from its title: ai, patterns, web or other (decides the colours and drawing of its cover)."""
    t = f" {title.casefold()} "
    return next((k for k, words in TOPICS if any(w in t for w in words)), "other")


def title_from_name(path):
    name = re.sub(r"\.pdf$", "", os.path.basename(path), flags=re.I)
    return A.clean_text(re.sub(r"[_\s]+", " ", name), 120) or "Untitled"


def add_book(src, dest_dir=None):
    """Copy a PDF into the books folder (never over another file). Returns the new path, or raises ValueError."""
    dest_dir = dest_dir or config()["dirs"][0]
    try:
        with open(src, "rb") as f:
            if b"%PDF-" not in f.read(1024):
                raise ValueError("that is not a PDF file")
    except OSError as exc:
        raise ValueError(f"cannot read it ({exc.strerror})") from exc
    os.makedirs(dest_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src).replace("\x00", ""))
    stem, ext = stem or "book", ".pdf"
    path, n = os.path.join(dest_dir, stem + ext), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(dest_dir, f"{stem} ({n}){ext}")
    shutil.copyfile(src, path)
    return path


# ------------------------------------------------------------------ what Poppler told us about each file (a cache)
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


class Index:
    """title, author and page count per file, so the library does not open every PDF each time it is shown."""

    def __init__(self, path=None):
        self.path = path or INDEX_FILE
        self.d = _read(self.path, {})

    def get(self, f):
        m = self.d.get(f["path"])
        return m if m and m.get("size") == f["size"] and m.get("mtime") == f["mtime"] else None

    def put(self, f, title, author, pages):
        self.d[f["path"]] = dict(size=f["size"], mtime=f["mtime"], title=A.clean_text(title, 160),
                                 author=A.clean_text(author, 120), pages=max(0, int(pages or 0)))

    def forget_missing(self, files):
        keep = {f["path"] for f in files}
        self.d = {p: m for p, m in self.d.items() if p in keep}

    def save(self):
        _write(self.path, self.d)


# ------------------------------------------------------------------ your data
def _num(v, default=0.0, lo=None, hi=None):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return min(hi, v) if hi is not None and v > hi else max(lo, v) if lo is not None and v < lo else v


def _day(now):
    return datetime.date.fromtimestamp(now).isoformat()


class Store:
    """Position, bookmarks, highlights, notes and the reading log of every book. Call save() after changes."""

    def __init__(self, path=None):
        self.path = path or DATA_FILE
        d = _read(self.path, {})
        self.books = d.get("books") if isinstance(d.get("books"), dict) else {}
        self.log = d.get("log") if isinstance(d.get("log"), dict) else {}
        self.prefs = d.get("prefs") if isinstance(d.get("prefs"), dict) else {}
        self.dirty = False

    def pref(self, key, default=None):
        """A choice made in the reader itself (the page theme)."""
        v = self.prefs.get(key, default)
        ok = {"theme": v in THEMES, "view": v in VIEWS}.get(key, True)
        return v if ok else default

    def set_pref(self, key, value):
        if self.prefs.get(key) != value:
            self.prefs[key], self.dirty = value, True

    def forget(self, bid):
        """Drop everything saved for one book (not the reading log)."""
        if self.books.pop(bid, None) is not None:
            self.dirty = True

    def save(self):
        _write(self.path, dict(books=self.books, log=self.log, prefs=self.prefs))
        self.dirty = False

    def flush(self):
        if self.dirty:
            self.save()

    # ---- one book
    FIELDS = dict(path=(str, ""), page=(int, 0), frac=((int, float), 0.0), furthest=(int, 0), pages=(int, 0),
                  opened=((int, float), 0), added=((int, float), 0), finished=(bool, False), bookmarks=(list, None),
                  highlights=(list, None), notes=(list, None), zoom=((int, float, type(None)), None))

    def book(self, bid):
        """The saved data of one book, with every field present and of the right kind (the file can be edited by hand)."""
        b = self.books.get(bid)
        if not isinstance(b, dict):
            b = self.books[bid] = {}
        for key, (kind, default) in self.FIELDS.items():
            cur = b.get(key)
            if key not in b or not isinstance(cur, kind) or (isinstance(cur, bool) and kind != bool):
                b[key] = [] if kind is list else default
        return b

    def seen(self, bid, path, now=None):
        """The library found this book. Remembers where it is and when it first appeared."""
        b = self.book(bid)
        if b["path"] != path:
            b["path"], self.dirty = path, True
        if not b["added"]:
            b["added"], self.dirty = now or time.time(), True
        return b

    def opened(self, bid, now=None):
        b = self.book(bid)
        b["opened"], self.dirty = now or time.time(), True
        return b

    def set_position(self, bid, page, frac, pages=None):
        """Where the reading is: a page (from 0) and how far down it. The book is finished when you get to its end."""
        b = self.book(bid)
        if pages:
            b["pages"] = int(pages)
        page = int(_num(page, 0, 0, max(0, b["pages"] - 1) if b["pages"] else None))
        frac = _num(frac, 0.0, 0.0, 1.0)
        if (page, round(frac, 3)) != (b["page"], round(b["frac"], 3)):
            b["page"], b["frac"], self.dirty = page, frac, True
        if page > b["furthest"]:
            b["furthest"], self.dirty = page, True
        if b["pages"] and not b["finished"] and (page + frac) / b["pages"] >= FINISHED_AT and page > 0:
            b["finished"], self.dirty = True, True
        return b

    def set_zoom(self, bid, zoom):
        b = self.book(bid)
        z = None if zoom is None else _num(zoom, 1.0, 0.1, 8.0)
        if b["zoom"] != z:
            b["zoom"], self.dirty = z, True

    def progress(self, bid):
        b = self.book(bid)
        if b["finished"]:
            return 1.0
        return min(1.0, (b["page"] + b["frac"]) / b["pages"]) if b["pages"] and b["opened"] else 0.0

    def status(self, bid):
        b = self.book(bid)
        if b["finished"]:
            return "finished"
        return "reading" if b["opened"] and (b["page"] or b["frac"] > 0.02) else "unread"

    def mark_finished(self, bid):
        b = self.book(bid)
        b["finished"], b["opened"], self.dirty = True, b["opened"] or time.time(), True

    def mark_unread(self, bid):
        b = self.book(bid)
        b.update(finished=False, page=0, frac=0.0, furthest=0)
        self.dirty = True

    # ---- bookmarks
    def toggle_bookmark(self, bid, page):
        marks = self.book(bid)["bookmarks"]
        page = int(_num(page, 0, 0))
        self.dirty = True
        if page in marks:
            marks.remove(page)
            return False
        marks.append(page)
        marks.sort()
        return True

    def bookmarks(self, bid):
        return [p for p in self.book(bid)["bookmarks"] if isinstance(p, int)]

    # ---- highlights and notes. A highlight is lines of text (rects in page points) and may carry a note.
    def _new_id(self, items):
        return max([i.get("id", 0) for i in items if isinstance(i, dict)] + [0]) + 1

    def add_highlight(self, bid, page, rects, text, color="yellow", now=None):
        b = self.book(bid)
        clean = []
        for r in list(rects)[:MAX_RECTS]:
            try:
                x1, y1, x2, y2 = (float(v) for v in r)
            except (TypeError, ValueError):
                continue
            if all(v == v and abs(v) < 1e7 for v in (x1, y1, x2, y2)):
                clean.append([round(min(x1, x2), 2), round(min(y1, y2), 2), round(max(x1, x2), 2), round(max(y1, y2), 2)])
        if not clean:
            return None
        h = dict(id=self._new_id(b["highlights"]), page=int(_num(page, 0, 0)), rects=clean,
                 text=re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT], color=color if color in COLORS else "yellow",
                 note="", at=now or time.time())
        b["highlights"].append(h)
        self.dirty = True
        return h["id"]

    def highlights(self, bid, page=None):
        return [h for h in self.book(bid)["highlights"]
                if isinstance(h, dict) and (page is None or h.get("page") == page)]

    def remove_highlight(self, bid, hid):
        b = self.book(bid)
        before = len(b["highlights"])
        b["highlights"] = [h for h in b["highlights"] if h.get("id") != hid]
        self.dirty = self.dirty or len(b["highlights"]) != before
        return len(b["highlights"]) != before

    def set_highlight_note(self, bid, hid, note):
        for h in self.book(bid)["highlights"]:
            if h.get("id") == hid:
                h["note"], self.dirty = str(note or "").strip()[:MAX_TEXT], True
                return True
        return False

    def set_highlight_color(self, bid, hid, color):
        for h in self.book(bid)["highlights"]:
            if h.get("id") == hid and color in COLORS:
                h["color"], self.dirty = color, True
                return True
        return False

    def add_note(self, bid, page, text, now=None):
        b = self.book(bid)
        text = str(text or "").strip()[:MAX_TEXT]
        if not text:
            return None
        n = dict(id=self._new_id(b["notes"]), page=int(_num(page, 0, 0)), text=text, at=now or time.time())
        b["notes"].append(n)
        self.dirty = True
        return n["id"]

    def notes(self, bid):
        return [n for n in self.book(bid)["notes"] if isinstance(n, dict)]

    def edit_note(self, bid, nid, text):
        text = str(text or "").strip()[:MAX_TEXT]
        b = self.book(bid)
        for n in b["notes"]:
            if n.get("id") == nid:
                if text:
                    n["text"] = text
                else:
                    b["notes"].remove(n)
                self.dirty = True
                return True
        return False

    def remove_note(self, bid, nid):
        return self.edit_note(bid, nid, "")

    # ---- the reading log: seconds and pages per day
    def _today(self, now):
        day = self.log.setdefault(_day(now), {})
        if not isinstance(day.get("secs"), (int, float)):
            day["secs"] = 0
        if not isinstance(day.get("books"), dict):
            day["books"] = {}
        if not isinstance(day.get("pages"), dict):
            day["pages"] = {}
        return day

    def add_time(self, bid, secs, now=None):
        now = now or time.time()
        secs = _num(secs, 0.0, 0.0, 3600.0)
        if secs <= 0:
            return
        day = self._today(now)
        day["secs"] += secs
        day["books"][bid] = day["books"].get(bid, 0) + secs
        self.dirty = True

    def add_page(self, bid, page, now=None):
        day = self._today(now or time.time())
        pages = day["pages"].setdefault(bid, [])
        if page not in pages:
            pages.append(int(page))
            self.dirty = True
            return True
        return False

    def prune(self, now=None):
        cutoff = _day((now or time.time()) - KEEP_DAYS * 86400)
        old = [d for d in self.log if d < cutoff]
        for d in old:
            del self.log[d]
        self.dirty = self.dirty or bool(old)

    def secs_on(self, day):
        d = self.log.get(day if isinstance(day, str) else day.isoformat())
        return int(d.get("secs", 0)) if isinstance(d, dict) else 0

    def pages_on(self, day):
        d = self.log.get(day if isinstance(day, str) else day.isoformat())
        return sum(len(p) for p in (d.get("pages") or {}).values()) if isinstance(d, dict) else 0

    def today(self, now=None):
        """(seconds, pages) read today"""
        today = datetime.date.fromtimestamp(now or time.time())
        return self.secs_on(today), self.pages_on(today)

    def week(self, now=None):
        """[(date, seconds)] for the last 7 days, today last"""
        today = datetime.date.fromtimestamp(now or time.time())
        return [(d, self.secs_on(d)) for d in (today - datetime.timedelta(days=i) for i in range(6, -1, -1))]

    def streak(self, now=None):
        """Days in a row with at least a minute of reading. Today not read yet does not break it."""
        day = datetime.date.fromtimestamp(now or time.time())
        if self.secs_on(day) < DAY_MIN_SECS:
            day -= datetime.timedelta(days=1)
        n = 0
        while self.secs_on(day) >= DAY_MIN_SECS:
            n += 1
            day -= datetime.timedelta(days=1)
        return n

    def book_secs(self, bid):
        return int(sum(d.get("books", {}).get(bid, 0) for d in self.log.values() if isinstance(d, dict)))

    def pages_seen(self, bid):
        return len({p for d in self.log.values() if isinstance(d, dict) for p in (d.get("pages") or {}).get(bid, [])})

    def minutes_left(self, bid):
        """About how long the rest of the book takes at your own pace with it; None before there is enough to tell."""
        b = self.book(bid)
        seen, secs = self.pages_seen(bid), self.book_secs(bid)
        if not b["pages"] or seen < 3 or secs < 60 or b["finished"]:
            return None
        return max(1, round((b["pages"] - b["page"] - 1) * secs / seen / 60))

    def finished_count(self):
        return sum(1 for b in self.books.values() if isinstance(b, dict) and b.get("finished"))


class Tracker:
    """Reading time and pages, from regular ticks while the reader is on screen. Time only counts while there was
    something to react to in the last 2 minutes (a key, a click, a scroll). Time is passed in, so it can be tested."""

    def __init__(self, store):
        self.store = store
        self.last = self.active = self.page_since = self.run_start = 0.0
        self.page, self.counted, self.broken = None, False, False

    def poke(self, now):
        self.active = now

    def stop(self):
        self.last = 0.0
        self.page = None

    def tick(self, now, bid, page):
        """Call about every 2 s with the book and the page in view; returns True when a new page was counted."""
        new = False
        if self.last and now - self.active <= IDLE_SECS:
            self.store.add_time(bid, min(now - self.last, 5.0), now)
            if not self.run_start:
                self.run_start = now
        elif now - self.active > 300:
            self.run_start = 0.0                                     # a real pause: the next stretch starts fresh
        self.last = now
        if (bid, page) != self.page:
            self.page, self.page_since, self.counted = (bid, page), now, False
        elif not self.counted and now - self.page_since >= PAGE_SEEN_SECS and now - self.active <= IDLE_SECS:
            self.counted = True
            new = self.store.add_page(bid, page, now)
        return new

    def break_due(self, now, minutes):
        """True once after `minutes` of reading without a real pause (an eye break); then the count starts again."""
        if minutes and self.run_start and now - self.run_start >= minutes * 60 and now - self.active <= IDLE_SECS:
            self.run_start = now
            return True
        return False


# ------------------------------------------------------------------ the library as a list
def library(store, index, files, now=None):
    """One entry per file: what the library shows. Titles come from the PDF when known, else from the file name."""
    out = []
    for f in files:
        bid = book_id(f["path"], f["size"])
        b = store.seen(bid, f["path"], now)
        meta = index.get(f) or {}
        pages = meta.get("pages") or b["pages"]
        name, sub, name_author = split_title(title_from_name(f["path"]))     # what the file name says (no PDF details yet)
        out.append(dict(id=bid, path=f["path"], title=meta.get("title") or (f"{name}: {sub}" if sub else name),
                        author=meta.get("author") or name_author, pages=pages, known=bool(meta), size=f["size"],
                        progress=store.progress(bid), status=store.status(bid), page=b["page"], opened=b["opened"],
                        added=b["added"], marks=len(b["bookmarks"]) + len(b["highlights"]) + len(b["notes"])))
    return out


def arrange(entries, query="", status="all", sort="recent"):
    """Filter by words in the title or author and by status (all, reading, unread, finished), then sort."""
    words = (query or "").casefold().split()
    shown = [e for e in entries
             if (status == "all" or e["status"] == status) and all(w in (e["title"] + " " + e["author"]).casefold() for w in words)]
    if sort == "title":
        shown.sort(key=lambda e: e["title"].casefold())
    elif sort == "progress":
        shown.sort(key=lambda e: (-e["progress"], e["title"].casefold()))
    else:
        shown.sort(key=lambda e: (-e["opened"], -e["added"], e["title"].casefold()))
    return shown


def continue_reading(entries, n=4):
    return sorted((e for e in entries if e["status"] == "reading"), key=lambda e: -e["opened"])[:n]


def fmt_minutes(minutes):
    """'<1m', '42m', '1h 05m'"""
    m = int(minutes)
    if m < 1:
        return "<1m"
    return f"{m}m" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def export_markdown(entry, store):
    """Highlights and notes of a book as Markdown, in page order (for Obsidian, a mail, a study group)."""
    bid = entry["id"]
    rows = [(h["page"], 0, h) for h in store.highlights(bid)] + [(n["page"], 1, n) for n in store.notes(bid)]
    out = [f"# {A.clean_text(entry['title'], 160)}"]
    if entry.get("author"):
        out.append(f"*{A.clean_text(entry['author'], 120)}*")
    out.append("")
    if not rows:
        return "\n".join(out + ["(no highlights or notes yet)", ""])
    for page, kind, item in sorted(rows, key=lambda r: (r[0], r[1], r[2].get("id", 0))):
        if kind == 0:
            out.append(f"- **p. {page + 1}** — > {item['text'] or '(highlight)'}")
            if item.get("note"):
                out.append(f"  - note: {item['note']}")
        else:
            out.append(f"- **p. {page + 1}** — note: {item['text']}")
    return "\n".join(out) + "\n"


def write_export(entry, store):
    """Save the highlights and notes next to the PDF as `<name>.notes.md` and return its path. The text goes to a new file
    that cannot be a link, then replaces the old one, so a link planted under that name is replaced, never followed."""
    path = os.path.splitext(entry["path"])[0] + ".notes.md"
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(export_markdown(entry, store))
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


# ------------------------------------------------------------------ command line
def main(argv):
    store, index = Store(), Index()
    if argv and argv[0] == "stats":
        secs, pages = store.today()
        goal = config()["daily_goal_minutes"]
        print(f"Today: {fmt_minutes(secs / 60)} of {goal}m, {pages} pages · streak {store.streak()} days · "
              f"{store.finished_count()} books finished")
        print("Last 7 days: " + "  ".join(f"{d.strftime('%a')} {fmt_minutes(s / 60)}" for d, s in store.week()))
        return 0
    entries = arrange(library(store, index, scan()), sort="recent")
    if not entries:
        print(f"No books yet. Put PDFs in {config()['dirs'][0]} (or drop them on the Books mode).")
        return 0
    for e in entries:
        pages = f"{e['page'] + 1}/{e['pages']}" if e["pages"] and e["status"] == "reading" else (str(e["pages"]) if e["pages"] else "?")
        print(f"{e['progress'] * 100:3.0f}%  {e['status']:8s} {pages:>9s}  {e['title']}")
    store.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
