"""Keyboard helpers shared by Kiln's windows."""


def latin_key(ev):
    """The key as it is on a US layout: with an Arabic (or Russian...) layout active Ctrl+V reaches us as the Arabic
    letter on that key, so shortcuts would not work. Falls back to the key as typed."""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    kv = ev.keyval
    if kv < 0x80:
        return kv
    try:
        km = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        for group in range(4):
            ok, val, _eg, _lvl, _cons = km.translate_keyboard_state(ev.hardware_keycode, shift, group)
            if ok and 0x20 <= val < 0x80:
                return val
    except (TypeError, ValueError):
        pass
    return kv
