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
* A book's cover is the first page of its PDF. When that page is blank, Kiln draws a cover from the title, author and subject
  instead (`books.covers`: `pdf` always uses the first page, `designed` draws one for every book). Titles, authors and page
  counts come from the PDFs; all of it is cached in `~/.cache/kiln`.
* **Continue reading** shows the books you started, newest first, with the page you stopped at, the percentage and about
  how long is left at your own pace with that book.
* The strip on top shows today's reading against your goal (the ring), your streak, the pages you read and the last
  7 days. A day counts for the streak from one minute of reading.
* Filter by All / Reading / Unread / Finished, sort by Recent / Title / Progress, and type to filter by title or author
  (`/` or Ctrl+F).
* Right-click a book: open, **set a cover image** (a PNG, JPEG or WebP picture, kept in `~/.config/kiln/covers`; "Use the
  automatic cover" undoes it), mark finished or unread, export its highlights and notes as Markdown (a `.notes.md` file next
  to the PDF) or show it in the folder. A book is marked finished when you reach 97% of it.
* Right-click → **Move to Trash…** removes a book: after you confirm, its PDF goes to the Trash (you can bring it back from
  there) and Kiln forgets its place, bookmarks, highlights, notes and cover. Only files inside a books folder are touched, and if
  the Trash cannot take the file nothing is changed. Your reading time per day is kept.

## The reader

The default view is the **open book**: two pages side by side with the cover on its own, one spread at a time, and the page
turns like paper. Turn with `→` `←` (or Space, Page Down/Up, the mouse wheel), click the left or right fifth of the page area,
or drag a page edge with the mouse: the page follows the pointer and turns on if you take it far enough, or falls back.
Text can be selected and highlighted as usual. Press `s` for the two scrolling views, which also zoom.

| Key | |
|---|---|
| Scroll, Space / Page Down, Page Up, ↑ ↓ (or `j` `k`) | move through the book (continuous scrolling); in the open book they turn the page |
| ← → (or `h` `l`), Home, End | previous or next page (spread), first or last page |
| `+` `-` `0`, Ctrl + wheel | zoom in, out, fit to the width (scrolling views) |
| `/` or Ctrl+F, Enter, Shift+Enter (or `]`) | search the whole book, next and previous match |
| `g` | go to a page |
| `t` / `m` | contents / bookmarks panel (`Esc` closes it) |
| `b` | bookmark this page |
| `n` | write a note on this page |
| `d` | page colours: Auto (dark in the evening), Light, Sepia, Night |
| `s` | the view: the **open book** (two pages, turning), two pages scrolling, one page scrolling |
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
