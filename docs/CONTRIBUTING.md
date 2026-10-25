# Contributing

Bug reports and new services are welcome.

* **Tests**: `python3 -m unittest discover -s tests` (no network, no accounts needed).
* **Adding a task or calendar service**: see the end of [ACCOUNTS.md](ACCOUNTS.md).
* **Themes**: see [THEMES.md](THEMES.md). A theme is one entry in a JSON file.
* **Style**: plain Python and bash, no new dependencies without a good reason, and the code in `share/` has to
  keep working on a laptop screen as well as a 3440 px one.

## Demo mode and screenshots

`tools/demo.py` runs Kiln on made-up data (projects, tasks, meetings, servers, a terminal session) so screenshots
never show your own work:

```bash
python3 tools/demo.py start                 # builds /tmp/kiln-demo, stops your Kiln, starts the demo
python3 tools/demo.py shoot desktop.png     # add "coding" or "focus" to choose the mode
python3 tools/demo.py settings accounts.png
python3 tools/demo.py gif tour.gif
python3 tools/demo.py stop                  # your own Kiln comes back
```

It needs X11, `xdotool`, ImageMagick and Pillow. Under the hood the app reads `KILN_DEMO=<file>` (a small JSON file
with the fake open projects, servers and busiest programs) and a throwaway `HOME`.
