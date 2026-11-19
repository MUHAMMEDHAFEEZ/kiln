# Views and arranging them

The work bar has a list of **views**: *Overview | Coding | Focus | Tasks | Books*, and a **+** at the end. Overview and Coding
have a free arrangement of widgets: every card (**Prayer**, **Today**, **Agenda**, **GitHub**, **Projects**, the **terminal**,
**Focus**, **Now playing**, the desktop **icons**, and the cards of the big screen: **Git, Tasks, System, Notes, Tests,
Calendar, Services, VPN**) is a window you can put anywhere. Focus, Tasks and Books keep Kiln's own arrangement.

## Your own views, hide and delete

* **+** (or *New view…* in any menu below) makes a **new view**: it starts as a copy of the one you are on, so it is "a new
  Overview" that you then arrange as you like. Give it a name; it appears in the list after Coding (or after your last view); drag it wherever you like.
* **Drag** a view left or right in the list to put it where you want (any view, the built-in ones too); a line shows where it
  will land. A click without dragging still goes to the view. **Move left** / **Move right** in the right-click menu do the
  same. A hidden view keeps its place in the order.
* **Right-click** a view in the list:
  * **Hide “Name”** takes it out of the list **without deleting anything** (works for Overview, Coding, Focus, Tasks, Books and
    your own views; at least one view stays in the list).
  * **Rename…** and **Delete…** (only for the views you made; the built-in ones can only be hidden).
  * **New view…**.
  * **Show “Name”** (at the end, one per hidden view) brings a hidden view back into the list and switches to it.
* The right-click menu of the grid button has the same entries for the view you are on.

```bash
kiln views                 # the views in the list
kiln views hidden          # the ones you hid
kiln mode Design           # switch to a view by its name (or: overview, coding, focus, tasks, books, next)
```

## Edit layout

Press the little grid button in the work bar, or run `kiln layout`. It arranges the view you are on (from Focus, Tasks or Books it
takes you to the Overview first). The widgets get a dashed border and the **Arrange your widgets** bar appears at the top; its
name button is the view you are arranging and opens the same menu as a right-click.

| | |
|---|---|
| **Drag** a widget | moves it anywhere. It **sticks** to the other widgets: edges and centres line up, and it lands next to another widget with the same gap Kiln uses (18 px) when it comes within about 14 px; lines show what it lined up with. Hold **Alt** to move freely (an 8-pixel grid). It always keeps part of itself on the screen |
| **Shift+click** widgets | picks several (a tick shows it); dragging one of the picked widgets moves them all together, keeping their places relative to each other |
| **Arrange** | with two or more picked: *Align* left, centres or right, top, middle or bottom; *Space evenly* left to right or top to bottom (three or more); *Line up in a row* / *Stack in a column* (one after the other with the 18 px gap); *Select none*. Also *Select all* and the switch **Stick to the other widgets** |
| **x** (top right of a widget) | takes it off this view (it is not deleted); the gallery puts it back |
| **Corner** (terminal, the dock cards and your own widgets) | changes the size; the right and bottom edges stick to the other widgets too (Alt = free) |
| **+ Add widget** | opens the **widget gallery** (below) |
| **Reset** | forgets the changes of this view |
| **Cancel** | leaves the edit mode and puts the places, the views and their order back as they were when you started |
| **Done** (or the grid button again) | saves and leaves the edit mode |

Every view has a place of its own for every widget: moving the Today card in one view does not move it in the others.
`kiln widgets` opens the gallery directly.

## The widget gallery

One window for everything you can put on a view, in three parts. A tile is one click: it adds the widget to the view you are
arranging (✓ On) or takes it off, and the desktop changes at once while the gallery stays open. The search box filters all of it.

* **Create new**: Note, Command output, Scripts, Shortcuts, Web page, Picture (below).
* **Kiln widgets**: the cards that come with Kiln: Prayer times, Today, Agenda, GitHub, Projects, Terminal, Focus timer, Now
  playing, Desktop icons, and the cards of the big screen: Git, Tasks, System, Notes, Tests, Calendar, Running, VPN. One that needs
  something you have not set up (a Lark Base board for Tasks, WARP for VPN) is greyed out and says what it needs.
* **My widgets**: the ones you built, kept here so you can put them on any view. The **⋯** of a tile (or a right-click) has
  *Edit…*, *Duplicate*, *Add to / Take off this view* and *Delete…* (deleting removes it from every view).

## Widgets you build

| Kind | What it is |
|---|---|
| **Note** | free text (Arabic reads right to left) |
| **Command output** | runs *your* command as you every 5 s to 1 hour (`df -h /`, `git -C ~/code/app status -sb`, `kubectl get pods`...) and shows what it prints; the ↻ button runs it now |
| **Scripts** | **a control panel of commands**: each entry is a row with a button. *Runs once*: a command or a whole script (several lines), run **in the background** (a dot shows whether it worked and its last line; right-click → *Show its last output…*) or **in a new terminal tab**. *Keeps running* (a server, an app): **▶ Start / ■ Stop**, a green dot and how long it has been running, its **log** (right-click → *Show its log…*), *Restart*. Kiln starts it in a group of its own, so Stop also stops what it started; Stop runs your **stop command** when you give one (`docker compose down`, `fuser -k 3000/tcp`) and otherwise sends SIGTERM, then SIGKILL after 5 s. A program Kiln started is still controlled after Kiln restarts. For something that is started some other way (docker, systemd) give a **check** command that succeeds while it runs: the button follows it. An entry can run in a **folder** you choose. The editor's **Add from a template** fills in the common ones: open an app, close an app, open a project in VS Code, a dev server, Docker compose up / down, a systemd user service, free a port, run the tests in a terminal. Up to 12 entries |
| **Shortcuts** | one line each, `Name = address`: a web address opens in the browser, a file or folder (`~/logs`) in its own app, anything else (`make -C ~/app test`) runs as a command in the background |
| **Web page** | a web page inside the widget (a dashboard, a board, a chat): address (`example.com`, `localhost:3000`), zoom, and a reload interval. Only http and https load; pop-ups, permission questions (location, camera, notifications) and downloads are refused; a link that wants a new window opens in your browser. Each widget has its own cookies (`~/.config/kiln/web/<id>`), so a login stays and is not shared. Needs `gir1.2-webkit2-4.1`; each page costs some memory |
| **Picture** | an image file (PNG, JPEG, WebP, GIF, SVG) scaled to fit |

Commands and scripts run as you, exactly as written: only put there what you would type yourself. While you arrange, a widget you
built also has an **Edit** button next to its x; right-click it for *Edit…*, *Hide from this view* and *Delete this widget…*.
Outside the edit mode, right-click the widget itself. The widgets are in `~/.config/kiln/widgets.json` (only you can read it);
their places are in `layout.json` under `custom_w1`, `custom_w2`...

## The file

The views and their places are in `~/.config/kiln/layout.json` (positions relative to the top-left corner of the screen):

```json
{ "version": 3,
  "custom": { "v1": "Design" },
  "hidden": ["focus"],
  "order": ["overview", "coding", "v1", "focus", "tasks", "books"],
  "places": {
    "overview": { "card_today": { "x": 816, "y": 456 }, "card_strip": { "hidden": true } },
    "coding":   { "dock_git": { "added": true, "x": 650, "y": 80, "w": 620, "h": 330 } },
    "v1":       { } } }
```

`custom` are the views you made, `hidden` the views that are out of the list, `order` the order of the list. In `places`, `hidden` takes a widget that is
normally on a view off it, `added` puts one there that is not. Window keys: `card_strip` (Prayer), `card_today`, `card_agenda`,
`card_github`, `card_projects`, `term`, `focus`, `music`, `icons`, `dock_git`, `dock_board`, `dock_system`, `dock_notes`,
`dock_tests`, `dock_day`, `dock_services`, `dock_vpn`, and `custom_w1`, `custom_w2`... for the widgets you built. A file from an
earlier Kiln is converted: the places of the Overview stay, and layouts you had named become views of their own. Delete the file
to start over. A change of screen size keeps everything reachable: positions are pulled back onto the screen.

CPU and RAM are not on the clock card any more: add the **System card** for them.
