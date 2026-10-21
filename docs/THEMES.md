# Themes

Kiln ships eight themes: `kiln-dark`, `paper-light`, `sepia`, `slate-ink`, `nordic-frost`,
`graphite-moss`, `graphite-lime` and `violet-dusk`.

```bash
kiln theme              # list them; the current one is marked
kiln theme nordic-frost # switch
```

## Your own theme

Create **`~/.config/kiln/themes.json`**. It has the same shape as
[`share/themes.json`](../share/themes.json). Its themes are added to the list, and one with the
same name as a built-in theme replaces it. The easiest start is to copy a built-in theme, rename
it and change the colours:

```json
{
  "order": ["ember"],
  "themes": {
    "ember": {
      "label": "Ember",
      "mode": "dark",
      "colors": { "...": "copy the colors block of kiln-dark and edit it" },
      "values": { "card_a": 0.94, "border_a": 0.9, "sheen_a": 0.03, "shadow_a": 0.55,
                  "panel_a": 0.94, "dock_a": 0.8 },
      "system": { "gtk_theme": "Yaru-dark", "icon_theme": "Yaru-dark",
                  "color_scheme": "prefer-dark", "folders": "warm", "folder_style": "matte",
                  "term_bg": "1c1b19", "term_fg": "ece6da", "term_cursor": "e0703a",
                  "term_palette": ["#2a2825", "..."] }
    }
  },
  "wallpapers": {
    "ember": { "kind": "ridges", "sky": ["1a1512", "241c17"],
               "layers": ["e0703a", "c75f30", "4a3f38", "362e29", "28221e", "1c1815"] }
  }
}
```

Then run `kiln theme ember`.

### `colors`

Six-digit hex without `#`.

| Key | Used for |
|---|---|
| `card`, `border`, `shadow` | Card background, outline and drop shadow |
| `text`, `dim`, `chip` | Main text, secondary text and small labels |
| `acc`, `acc2`, `acc_hi`, `on_acc` | Accent (buttons, highlights), second accent, light accent, text on the accent |
| `sel`, `on_sel` | Selection and the text on it |
| `ink` | `ffffff` for dark themes, `000000` for light ones (used for hover tints) |
| `ph1`, `ph2` | Placeholder gradient (empty artwork, thumbnails) |
| `violet`, `blue`, `cyan`, `teal`, `amber`, `mint`, `red`, `yellow`, `green` | Status colours: checkboxes, CI states, dirty badges, dots |
| `menu`, `menu_fg` | Pop-up menus, the launcher and notifications |
| `panel`, `panel_fg` | Top bar |
| `dock` | Dock background |

### `values`

| Key | Meaning |
|---|---|
| `card_a`, `border_a`, `sheen_a`, `shadow_a` | Card opacity, outline opacity, top highlight and shadow strength (0–1) |
| `panel_a`, `dock_a` | Top bar and dock opacity |
| `badge_mono` | `true` draws language badges in the accent colour instead of per-language colours |
| `warm_ci` | `true` draws "CI passing" in the accent instead of green |
| `mute` | 0–1. Softens bright status colours so they sit well in calm themes. |

### `system`

| Key | Meaning |
|---|---|
| `gtk_theme`, `icon_theme`, `color_scheme` | GNOME interface settings (`prefer-dark` / `prefer-light` / `default`) |
| `folders` | Which `folder_palettes` entry colours the desktop folders (`muted`, `graphite`, `vivid`, `warm`, or your own) |
| `folder_style` | `matte` (flat paper folders) or `glossy` |
| `project_palette` | Colours for project dots |
| `term_bg`, `term_fg`, `term_cursor`, `term_palette` | GNOME Terminal profile colours (16-colour palette) |

### Folder palettes

```json
"folder_palettes": { "sand": { "home": "#7d9cc8", "cycle": ["#d9714f", "#c9b9a0", "#9fb38f"] } }
```

The `cycle` colours go round your desktop folders in grid order, and `home` colours the Home
icon. A colour you set on one folder from its right-click menu stays until the next
`kiln theme`.

## Wallpapers

Every built-in theme has a generated paper-cut mountain wallpaper in
`~/.local/share/kiln/wallpapers`. To use your own picture, save it as
`~/.config/kiln/wallpapers/<theme>.jpg` (or `.png` / `.webp`) and run `kiln theme <theme>` again.

To generate a wallpaper from colours, give the theme an entry under `"wallpapers"` in your
`themes.json`:

* **`sky`**: the top and bottom colours of the sky gradient.
* **`layers`**: the mountain ranges from the back to the front. Six works well.

Then run `kiln wallpapers`.
