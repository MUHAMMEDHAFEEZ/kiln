# Books mode

A fifth mode for your PDF books: a library with covers and **Continue reading**, and a reader with bookmarks, highlights,
notes, search, a table of contents, sepia and night pages and reading statistics. Switch to it from the work bar, with
`Super+J`, or `kiln mode books`.

It needs Poppler's GTK bindings once (the mode tells you when they are missing):

```bash
sudo apt install gir1.2-poppler-0.18
```

## The library

* Put PDFs in `~/Books` (or the folder in Settings → Desktop → Books). Folders inside it count too. You can also press
  **Add book**, or drop PDF files on the mode; they are copied into the books folder and never replace a file.
* Covers (the first page), titles, authors and page counts come from the PDFs and are cached in `~/.cache/kiln`.
* **Continue reading** shows the books you started, newest first, with the page you stopped at, the percentage and about
  how long is left at your own pace with that book.
* The strip on top shows today's reading against your goal (the ring), your streak, the pages you read and the last
  7 days. A day counts for the streak from one minute of reading.
* Filter by All / Reading / Unread / Finished, sort by Recent / Title / Progress, and type to filter by title or author
  (`/` or Ctrl+F).
* Right-click a book: open, mark finished or unread, export its highlights and notes as Markdown (a `.notes.md` file next
  to the PDF) or show it in the folder. A book is marked finished when you reach 97% of it.

## The reader

| Key | |
|---|---|
| Scroll, Space / Page Down, Page Up, ↑ ↓ (or `j` `k`) | move through the book (continuous scrolling) |
| ← → (or `h` `l`), Home, End | previous or next page, first or last page |
| `+` `-` `0`, Ctrl + wheel | zoom in, out, fit to the width |
| `/` or Ctrl+F, Enter, Shift+Enter (or `]`) | search the whole book, next and previous match |
| `g` | go to a page |
| `t` / `m` | contents / bookmarks panel (`Esc` closes it) |
| `b` | bookmark this page |
| `n` | write a note on this page |
| `d` | page colours: Auto (dark in the evening), Light, Sepia, Night |
| `s` | two pages side by side (like an open book, the cover alone) or one page at a time |
| Backspace | back to where you were before a link, a search hit or a jump |
| Ctrl+C | copy the selected text |
| Esc | clear the selection, close search or the panel, then go back to the library |

* **Select text** with the mouse (double-click a word). A bar appears with **Copy**, four highlight colours and **Note**.
  Click a saved highlight to recolour it, add or edit its note, copy it or delete it. Highlights are kept per page, so
  select within one page.
* The side panel lists the **table of contents** (the chapter you are in is marked), your **bookmarks** and your
  **highlights and notes**; click one to jump there.
* The bar at the bottom shows how far you are; click or drag it to move. Red ticks are bookmarks, coloured ticks
  highlights.
* Links inside the book jump to their page; web links open in your browser.
* Kiln remembers the page, how far down it, and the zoom of every book.

## Your data

Everything you do is stored in `~/.config/kiln/books.json` (permissions 0600): where you stopped, bookmarks,
highlights, notes and the reading log. It is never sent anywhere. The PDFs are not changed; highlights and notes live in
this file, not inside the PDF.

Reading time counts only while there was a key, a click or a scroll in the last 2 minutes, and a page counts as read
after being in view for 4 seconds.

## Eye comfort

* **Sepia** warms the paper, **Night** turns the page dark and soft (never pure black on pure white). Images are inverted
  too in Night. **Auto** switches to Night from 19:00 to 06:00.
* After `books.eye_break_minutes` (30) minutes of reading without a pause, Kiln shows a reminder and a notification to
  look at something far away for 20 seconds. Set it to `0` to turn it off, or turn notifications off with
  `notifications.books: false`.

## Settings

See [CONFIGURATION.md](CONFIGURATION.md#books): `books.dirs`, `books.theme`, `books.daily_goal_minutes`,
`books.eye_break_minutes`.

`kiln books` lists the library with your progress; `kiln books stats` prints today, the week and the streak.
