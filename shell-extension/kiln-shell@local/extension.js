// Kiln shell: re-skins GNOME's top bar from the inside, so the real Wi-Fi / volume /
// battery / power menus, notifications and the calendar keep working.
//   left   : workspace badge + desktop name (opens Activities, scroll = switch workspace)
//   centre : search pill (opens the Kiln launcher, Super+K)
//   right  : date + time, then the usual indicators and quick settings
// Lock screen: crisp (unblurred) wallpaper and, under the clock, status chips (counts only —
// no task or meeting titles), Now Playing with controls, a live Focus countdown, the next
// prayer (computed offline, when a location is set), and day / week progress.
// Settings come from ~/.config/kiln/config.json (see docs/CONFIGURATION.md).
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as UnlockDialogMod from 'resource:///org/gnome/shell/ui/unlockDialog.js';

import * as Prayer from './prayer.js';

const TILE_BAR = 150;                                 // px, matches .kiln-lock-bar
const ROW_BAR = 110;                                  // px, matches .kiln-lock-bar-row
const SLOW_EVERY = 15;                                // seconds between chip/prayer refreshes
const PRAYER_NAMES = {
    ar: {Fajr: 'الفجر', Dhuhr: 'الظهر', Asr: 'العصر', Maghrib: 'المغرب', Isha: 'العشاء'},
    en: {Fajr: 'Fajr', Dhuhr: 'Dhuhr', Asr: 'Asr', Maghrib: 'Maghrib', Isha: 'Isha'},
};

export default class KilnShell extends Extension {
    enable() {
        const panel = Main.panel;
        this._home = GLib.get_home_dir();
        this._conf = `${this._home}/.config/kiln`;
        this._cache = `${this._home}/.cache/kiln`;
        this._share = `${this._home}/.local/share/kiln`;
        this._loadConfig();
        // desktop_name may use {Host} (capitalised machine name), {host} and {user}
        const host = GLib.get_host_name() || GLib.get_user_name();
        this._name = String(this._cfg.desktop_name ?? "{Host}'s Desktop")
            .replaceAll('{Host}', host.charAt(0).toUpperCase() + host.slice(1))
            .replaceAll('{host}', host)
            .replaceAll('{user}', GLib.get_user_name());

        // --- left: workspace badge + name (replaces the Activities button)
        const activities = panel.statusArea.activities;
        this._activitiesWasVisible = activities.visible;
        activities.hide();

        this._brand = new St.Button({
            style_class: 'kiln-brand', reactive: true, can_focus: true, track_hover: true,
        });
        const brandBox = new St.BoxLayout({style_class: 'kiln-brand-box'});
        this._wsLabel = new St.Label({
            text: '1',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._ws = new St.Bin({
            style_class: 'kiln-ws',
            y_align: Clutter.ActorAlign.CENTER,
            child: this._wsLabel,
        });
        brandBox.add_child(this._ws);
        brandBox.add_child(new St.Label({
            text: this._name,
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'kiln-brand-label',
        }));
        brandBox.add_child(new St.Icon({
            icon_name: 'pan-down-symbolic',
            style_class: 'kiln-chevron',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        this._brand.set_child(brandBox);
        this._brand.connect('clicked', () => Main.overview.toggle());
        const wm = global.workspace_manager;
        const updateWs = () => {
            this._wsLabel.text = `${wm.get_active_workspace_index() + 1}`;
        };
        this._wsSignals = [
            wm.connect('active-workspace-changed', updateWs),
            wm.connect('notify::n-workspaces', updateWs),
        ];
        updateWs();
        this._brand.connect('scroll-event', (_actor, event) => {
            const dir = event.get_scroll_direction();
            let i = wm.get_active_workspace_index();
            if (dir === Clutter.ScrollDirection.UP)
                i -= 1;
            else if (dir === Clutter.ScrollDirection.DOWN)
                i += 1;
            else
                return Clutter.EVENT_PROPAGATE;
            i = Math.max(0, Math.min(wm.get_n_workspaces() - 1, i));
            wm.get_workspace_by_index(i).activate(global.get_current_time());
            return Clutter.EVENT_STOP;
        });
        panel._leftBox.insert_child_at_index(this._brand, 0);

        // --- centre: search pill
        this._pill = new St.Button({
            style_class: 'kiln-pill', reactive: true, can_focus: true, track_hover: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        const pillBox = new St.BoxLayout({style_class: 'kiln-pill-box'});
        pillBox.add_child(new St.Icon({
            icon_name: 'system-search-symbolic',
            style_class: 'kiln-pill-icon',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        pillBox.add_child(new St.Label({
            text: 'Search anything…',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'kiln-pill-label',
        }));
        pillBox.add_child(new St.Label({
            text: 'Super + K',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'kiln-pill-hint',
        }));
        this._pill.set_child(pillBox);
        this._pill.connect('clicked', () => {
            try {
                GLib.spawn_command_line_async(`${this._share}/search.sh`);
            } catch (e) {
                console.error(`kiln-shell: ${e.message}`);
            }
        });
        panel._centerBox.insert_child_at_index(this._pill, 0);

        // --- right: move the date/time menu next to the indicators
        const dateMenu = panel.statusArea.dateMenu;
        this._dateContainer = dateMenu.container;
        this._dateParent = this._dateContainer.get_parent();
        this._dateParent.remove_child(this._dateContainer);
        panel._rightBox.insert_child_at_index(this._dateContainer, 0);
        this._dateContainer.add_style_class_name('kiln-date');

        // --- lock screen
        const proto = UnlockDialogMod.UnlockDialog.prototype;
        this._origBgEffects = proto._updateBackgroundEffects;
        const ext = this;
        proto._updateBackgroundEffects = function () {
            const look = ext._cfg.lock_screen ?? {};
            for (const widget of this._backgroundGroup) {
                const effect = widget.get_effect('blur');
                if (effect)
                    effect.set({brightness: look.brightness ?? 0.72, radius: look.blur ?? 0});
            }
        };
        this._shieldIds = [
            Main.screenShield.connect('active-changed', () => this._onShield()),
            Main.screenShield.connect('locked-changed', () => this._onShield()),
        ];
        this._modeId = Main.sessionMode.connect('updated', () => this._syncSessionMode());
        this._syncSessionMode();
        this._onShield();
    }

    disable() {
        const panel = Main.panel;

        // lock screen
        if (this._lockTimer)
            GLib.source_remove(this._lockTimer);
        this._lockTimer = 0;
        if (this._lockIdle)
            GLib.source_remove(this._lockIdle);
        this._lockIdle = 0;
        for (const id of this._shieldIds ?? [])
            Main.screenShield.disconnect(id);
        this._shieldIds = null;
        if (this._modeId)
            Main.sessionMode.disconnect(this._modeId);
        this._modeId = 0;
        if (this._origBgEffects)
            UnlockDialogMod.UnlockDialog.prototype._updateBackgroundEffects = this._origBgEffects;
        this._origBgEffects = null;
        const d = Main.screenShield._dialog;
        if (d?._vdCard) {
            d._vdCard.destroy();
            d._vdCard = null;
        }
        this._lk = null;
        this._lockedAt = null;
        this._upower = null;

        if (this._wsSignals) {
            for (const id of this._wsSignals)
                global.workspace_manager.disconnect(id);
        }
        this._wsSignals = null;

        if (this._dateContainer) {
            this._dateContainer.remove_style_class_name('kiln-date');
            const cur = this._dateContainer.get_parent();
            if (cur)
                cur.remove_child(this._dateContainer);
            if (this._dateParent)
                this._dateParent.insert_child_at_index(this._dateContainer, 0);
        }
        this._dateContainer = null;
        this._dateParent = null;

        this._pill?.destroy();
        this._pill = null;
        this._brand?.destroy();
        this._brand = null;

        if (this._activitiesWasVisible)
            panel.statusArea.activities.show();
    }

    // brand + search make no sense on the lock screen
    _syncSessionMode() {
        const locked = Main.sessionMode.isLocked;
        if (this._brand)
            this._brand.visible = !locked;
        if (this._pill)
            this._pill.visible = !locked;
    }

    _onShield() {
        if (!Main.screenShield.active && !Main.screenShield.locked) {
            if (this._lockTimer)
                GLib.source_remove(this._lockTimer);
            this._lockTimer = 0;
            this._lockedAt = null;
            this._lk = null;
            return;
        }
        if (!this._lockedAt)
            this._loadConfig();
        this._lockedAt ??= GLib.DateTime.new_now_local();
        if (!this._lockIdle) {
            this._lockIdle = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                this._lockIdle = 0;
                this._tickLock(true);
                return GLib.SOURCE_REMOVE;
            });
        }
        // one-second tick while locked: the focus countdown and the player state are live
        this._lockTimer ||= GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._tickLock(false);
            return GLib.SOURCE_CONTINUE;
        });
    }

    _tickLock(full) {
        try {
            const d = Main.screenShield._dialog;
            if (!d || !d._clock)
                return;
            if ((this._cfg.lock_screen ?? {}).widgets === false)
                return;
            if (!this._lk || this._lk.dialog !== d) {
                this._buildLock(d);
                full = true;
            }
            this._lkTicks = ((this._lkTicks ?? 0) + 1) % SLOW_EVERY;
            this._updateFocus();
            this._updateMedia();
            if (full || this._lkTicks === 0) {
                this._updateChips();
                this._updatePrayer();
                this._updateProgress();
            }
        } catch (e) {
            console.error(`kiln-shell lock screen: ${e.message}`);
        }
    }

    _loadConfig() {
        try {
            this._cfg = JSON.parse(this._read(`${this._conf}/config.json`) || '{}') ?? {};
        } catch (e) {
            this._cfg = {};
        }
    }

    // clock text following the "clock" setting ("12h" / "24h")
    _hm(dt, withPeriod = true) {
        if (String(this._cfg.clock ?? '12h').startsWith('24'))
            return dt.format('%H:%M');
        return dt.format(withPeriod ? '%-I:%M %p' : '%-I:%M');
    }

    _label(text, cls) {
        return new St.Label({text, style_class: cls, y_align: Clutter.ActorAlign.CENTER});
    }

    _tile(head) {
        const t = new St.BoxLayout({style_class: 'kiln-lock-tile', vertical: true});
        t.add_child(this._label(head, 'kiln-lock-head'));
        return t;
    }

    _bar(extra = '') {
        const bar = new St.Widget({
            style_class: `kiln-lock-bar ${extra}`,
            layout_manager: new Clutter.BinLayout(),
            y_align: Clutter.ActorAlign.CENTER,
        });
        bar._fill = new St.Widget({
            style_class: 'kiln-lock-fill',
            x_align: Clutter.ActorAlign.START,
            y_expand: true,
        });
        bar.add_child(bar._fill);
        return bar;
    }

    _setBar(bar, frac, width) {
        const w = Math.round(Math.max(0, Math.min(1, frac)) * width);
        bar._fill.set_style(`width: ${w}px;`);
    }

    _dur(sec) {
        const m = Math.max(1, Math.round(sec / 60));
        return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m} min`;
    }

    _buildLock(d) {
        if (d._vdCard)
            d._vdCard.destroy();
        const lk = {dialog: d, artUrl: null, artFound: false};
        lk.root = new St.BoxLayout({style_class: 'kiln-lock', vertical: true, x_align: Clutter.ActorAlign.CENTER});

        lk.chips = new St.BoxLayout({style_class: 'kiln-lock-card', x_align: Clutter.ActorAlign.CENTER});
        lk.root.add_child(lk.chips);

        const tiles = new St.BoxLayout({style_class: 'kiln-lock-tiles'});
        lk.root.add_child(tiles);

        // now playing: art, title / artist, previous · play-pause · next
        lk.media = new St.BoxLayout({style_class: 'kiln-lock-tile kiln-lock-media', visible: false, x_expand: true});
        lk.art = new St.Bin({
            style_class: 'kiln-lock-art',
            y_align: Clutter.ActorAlign.CENTER,
            child: new St.Icon({icon_name: 'audio-x-generic-symbolic', style_class: 'kiln-lock-art-icon'}),
        });
        lk.media.add_child(lk.art);
        const mtext = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        mtext.add_child(this._label('NOW PLAYING', 'kiln-lock-head'));
        lk.title = this._label('', 'kiln-lock-title');
        lk.artist = this._label('', 'kiln-lock-sub');
        mtext.add_child(lk.title);
        mtext.add_child(lk.artist);
        const ctl = new St.BoxLayout({style_class: 'kiln-lock-controls'});
        const button = (icon, fn) => {
            const b = new St.Button({
                style_class: 'kiln-lock-btn',
                can_focus: false,
                child: new St.Icon({icon_name: icon, style_class: 'kiln-lock-btn-icon'}),
            });
            b.connect('clicked', () => {
                try {
                    const p = this._player();
                    if (p)
                        fn(p);
                } catch (e) {
                    console.error(`kiln-shell media: ${e.message}`);
                }
                this._keepClock(d);
            });
            ctl.add_child(b);
            return b;
        };
        button('media-skip-backward-symbolic', p => p.previous());
        lk.play = button('media-playback-start-symbolic', p => p.playPause());
        button('media-skip-forward-symbolic', p => p.next());
        mtext.add_child(ctl);
        lk.media.add_child(mtext);
        tiles.add_child(lk.media);

        // focus countdown (only while a session is running or paused part-way)
        lk.focus = this._tile('FOCUS');
        lk.focus.x_expand = true;
        lk.focus.visible = false;
        lk.fBig = this._label('', 'kiln-lock-big');
        lk.fSub = this._label('', 'kiln-lock-sub');
        lk.fBar = this._bar();
        lk.focus.add_child(lk.fBig);
        lk.focus.add_child(lk.fSub);
        lk.focus.add_child(lk.fBar);
        tiles.add_child(lk.focus);

        // next prayer
        lk.prayer = this._tile('NEXT PRAYER');
        lk.prayer.x_expand = true;
        const prow = new St.BoxLayout({style_class: 'kiln-lock-prow'});
        const pnext = new St.BoxLayout({vertical: true, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        lk.pBig = this._label('', 'kiln-lock-big');
        lk.pSub = this._label('', 'kiln-lock-sub');
        pnext.add_child(lk.pBig);
        pnext.add_child(lk.pSub);
        prow.add_child(pnext);
        // the whole day's times, shown when nothing is playing so the card fills the row
        lk.pDay = new St.BoxLayout({style_class: 'kiln-lock-pday', y_align: Clutter.ActorAlign.CENTER});
        lk.pCols = {};
        for (const n of Prayer.NAMES) {
            const col = new St.BoxLayout({style_class: 'kiln-lock-pcol', vertical: true});
            const nm = new St.Label({text: this._prayerName(n), style_class: 'kiln-lock-pname', x_align: Clutter.ActorAlign.CENTER});
            const tm = new St.Label({text: '', style_class: 'kiln-lock-ptime', x_align: Clutter.ActorAlign.CENTER});
            col.add_child(nm);
            col.add_child(tm);
            lk.pDay.add_child(col);
            lk.pCols[n] = {col, tm};
        }
        prow.add_child(lk.pDay);
        lk.prayer.add_child(prow);
        tiles.add_child(lk.prayer);

        // day / week progress + when the screen was locked
        const prog = new St.BoxLayout({style_class: 'kiln-lock-progress', x_align: Clutter.ActorAlign.CENTER});
        const seg = name => {
            const b = new St.BoxLayout({style_class: 'kiln-lock-seg'});
            const bar = this._bar('kiln-lock-bar-row');
            const value = this._label('', 'kiln-lock-seg-value');
            b.add_child(this._label(name, 'kiln-lock-seg-label'));
            b.add_child(bar);
            b.add_child(value);
            prog.add_child(b);
            return {bar, value};
        };
        lk.day = seg('Day');
        lk.week = seg('Week');
        lk.since = this._label('', 'kiln-lock-seg-label');
        prog.add_child(lk.since);
        lk.root.add_child(prog);

        d._clock.add_child(lk.root);
        d._vdCard = lk.root;
        d._updateBackgroundEffects?.();
        this._lk = lk;
    }

    // a click on the media buttons must not also open the password prompt
    _keepClock(d) {
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            if (d === Main.screenShield._dialog && d._activePage !== d._clock)
                d._showClock?.();
            this._updateMedia();
            return GLib.SOURCE_REMOVE;
        });
    }

    _updateChips() {
        const chips = this._lk.chips;
        chips.destroy_all_children();
        for (const [icon, label] of this._lockStats()) {
            const chip = new St.BoxLayout({style_class: 'kiln-lock-chip'});
            chip.add_child(new St.Icon({
                icon_name: icon,
                style_class: 'kiln-lock-icon',
                y_align: Clutter.ActorAlign.CENTER,
            }));
            chip.add_child(this._label(label, 'kiln-lock-label'));
            chips.add_child(chip);
        }
    }

    // GNOME already tracks MPRIS players for the calendar's media section; reuse them
    _player() {
        const players = Main.panel.statusArea.dateMenu?._messageList?._mediaSection?._players;
        let pick = null;
        const ytm = /youtube-?music|youtube_music|pear|ytmdesktop/;
        for (const p of players?.values() ?? []) {
            if (!p._visible)
                continue;
            if ((this._cfg.now_playing ?? {}).youtube_music_only && !ytm.test(`${p._busName ?? ''} ${p._mprisProxy?.Identity ?? ''}`.toLowerCase()))
                continue;                       // only the YouTube Music app
            if (p.status === 'Playing')
                return p;
            pick ??= p;
        }
        return pick;
    }

    _updateMedia() {
        const lk = this._lk;
        if (!lk)
            return;
        const p = this._player();
        lk.media.visible = !!p;
        lk.pDay.visible = !p;
        if (!p)
            return;
        lk.title.text = p.trackTitle || 'Unknown title';
        lk.artist.text = (p.trackArtists ?? []).join(', ');
        lk.play.child.icon_name = p.status === 'Playing'
            ? 'media-playback-pause-symbolic' : 'media-playback-start-symbolic';

        const url = p.trackCoverUrl || '';
        if (url === lk.artUrl && lk.artFound)
            return;
        lk.artUrl = url;
        let file = null;
        if (url) {
            // the desktop widget caches artwork (Brave's own copy sits in its snap-private /tmp)
            const sum = GLib.compute_checksum_for_string(GLib.ChecksumType.SHA1, url, -1);
            const cached = `${this._cache}/art/${sum}.img`;
            if (GLib.file_test(cached, GLib.FileTest.EXISTS)) {
                file = Gio.File.new_for_path(cached);
            } else if (url.startsWith('file://')) {
                const f = Gio.File.new_for_uri(url);
                if (f.query_exists(null))
                    file = f;
            }
        }
        lk.artFound = !!file;
        lk.art.child.visible = !file;
        lk.art.set_style(file ? `background-image: url("${file.get_uri().replace(/"/g, '%22')}");` : null);
    }

    _updateFocus() {
        const lk = this._lk;
        let f = null;
        try {
            f = JSON.parse(this._read(`${this._cache}/focus.json`) || 'null');
        } catch (e) {}
        const now = Date.now() / 1000;
        const today = GLib.DateTime.new_now_local().format('%Y-%m-%d');
        let left = null, paused = false;
        if (f?.running && f.end_at > now) {
            left = f.end_at - now;
        } else if (f && !f.running && f.date === today && f.left > 0 && f.length && f.left < f.length - 1) {
            left = f.left;
            paused = true;
        }
        lk.focus.visible = left !== null;
        if (left === null)
            return;
        const m = Math.floor(left / 60), s = Math.floor(left % 60);
        lk.fBig.text = `${m}:${String(s).padStart(2, '0')}`;
        const what = f.mode === 'break' ? 'Break' : `Focus · ${Math.round(f.length / 60)} min`;
        lk.fSub.text = paused ? `${what} · paused` : what;
        this._setBar(lk.fBar, 1 - left / f.length, TILE_BAR);
    }

    _prayerName(n) {
        const lang = (this._cfg.prayer ?? {}).language ?? 'ar';
        return (PRAYER_NAMES[lang] ?? PRAYER_NAMES.en)[n] ?? n;
    }

    _updatePrayer() {
        const lk = this._lk;
        const pr = this._cfg.prayer ?? {};
        const loc = this._cfg.location ?? {};
        if (!pr.enabled || typeof loc.latitude !== 'number' || typeof loc.longitude !== 'number') {
            lk.prayer.visible = false;
            return;
        }
        const now = GLib.DateTime.new_now_local();
        let next = null, times = null;
        for (let add = 0; add < 2 && !next; add++) {
            const day = now.add_days(add);
            const [y, mo, dd] = [day.get_year(), day.get_month(), day.get_day_of_month()];
            const tz = day.get_utc_offset() / 3.6e9;
            const t = Prayer.prayerTimes(y, mo, dd, loc.latitude, loc.longitude, tz, pr.method ?? 'mwl', pr.asr ?? 'standard');
            const midnight = GLib.DateTime.new_local(y, mo, dd, 0, 0, 0);
            for (const name of Prayer.NAMES) {
                const at = midnight.add_seconds(Math.round(t[name] * 3600));
                if (at.to_unix() > now.to_unix()) {
                    next = [name, at];
                    break;
                }
            }
            times = {t, midnight};
        }
        lk.prayer.visible = !!next;
        if (!next)
            return;
        const [name, at] = next;
        lk.pBig.text = this._prayerName(name);
        lk.pSub.text = `${this._hm(at)} · in ${this._dur(at.to_unix() - now.to_unix())}`;
        for (const n of Prayer.NAMES) {
            const c = lk.pCols[n];
            c.tm.text = times.midnight.add_seconds(Math.round(times.t[n] * 3600)).format(String(this._cfg.clock ?? '12h').startsWith('24') ? '%H:%M' : '%-I:%M');
            if (n === name)
                c.col.add_style_pseudo_class('next');
            else
                c.col.remove_style_pseudo_class('next');
        }
    }

    _updateProgress() {
        const lk = this._lk;
        const now = GLib.DateTime.new_now_local();
        const dayF = (now.get_hour() * 3600 + now.get_minute() * 60 + now.get_second()) / 86400;
        const weekF = (now.get_day_of_week() % 7 + dayF) / 7;   // the week starts on Sunday
        this._setBar(lk.day.bar, dayF, ROW_BAR);
        this._setBar(lk.week.bar, weekF, ROW_BAR);
        lk.day.value.text = `${Math.round(dayF * 100)}%`;
        lk.week.value.text = `${Math.round(weekF * 100)}%`;
        lk.since.text = this._lockedAt ? `Locked ${this._hm(this._lockedAt)}` : '';
    }

    _battery() {
        try {
            this._upower ??= Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START, null, 'org.freedesktop.UPower',
                '/org/freedesktop/UPower/devices/DisplayDevice', 'org.freedesktop.UPower.Device', null);
            const g = k => this._upower.get_cached_property(k)?.unpack();
            if (!g('IsPresent'))
                return null;
            const state = g('State');
            let label = `${Math.round(g('Percentage'))}%`;
            if (state === 1)
                label += g('TimeToFull') > 0 ? ` · full in ${this._dur(g('TimeToFull'))}` : ' · charging';
            else if (state === 2 && g('TimeToEmpty') > 0)
                label += ` · ${this._dur(g('TimeToEmpty'))} left`;
            else if (state === 4)
                label += ' · full';
            return [g('IconName') || 'battery-symbolic', label];
        } catch (e) {
            this._upower = null;
            return null;
        }
    }

    _read(path) {
        try {
            const [ok, bytes] = Gio.File.new_for_path(path).load_contents(null);
            return ok ? new TextDecoder().decode(bytes) : null;
        } catch (e) {
            return null;
        }
    }

    _walk(o, fn) {
        if (Array.isArray(o)) {
            o.forEach(v => this._walk(v, fn));
        } else if (o && typeof o === 'object') {
            fn(o);
            Object.values(o).forEach(v => this._walk(v, fn));
        }
    }

    _ts(v) {
        if (v === null || v === undefined || v === '' || v === 0 || v === '0')
            return null;
        if (typeof v === 'object') {
            for (const k of ['timestamp', 'date_time', 'datetime', 'time', 'date']) {
                if (v[k])
                    return this._ts(v[k]);
            }
            return null;
        }
        const n = Number(v);
        if (!Number.isNaN(n))
            return n > 1e11 ? n / 1000 : n;
        const d = Date.parse(v);
        return Number.isNaN(d) ? null : d / 1000;
    }

    // counts only: nothing private is printed on the lock screen
    _lockStats() {
        const app = this._conf;
        const cache = this._cache;
        const out = [];

        let open = 0;
        const todo = this._read(`${app}/todo.txt`);
        if (todo)
            open += todo.split('\n').filter(l => l.startsWith('[ ]')).length;
        const lark = this._read(`${cache}/lark-tasks.json`);
        if (lark) {
            try {
                const seen = new Set();
                this._walk(JSON.parse(lark), o => {
                    const g = o.guid || o.task_guid;
                    if (g && typeof (o.summary ?? o.title) === 'string' && !this._ts(o.completed_at))
                        seen.add(g);
                });
                open += seen.size;
            } catch (e) {}
        }
        out.push(['object-select-symbolic', open ? `${open} task${open === 1 ? '' : 's'} left` : 'All tasks done']);

        const agenda = this._read(`${cache}/lark-agenda.json`);
        if (agenda) {
            try {
                const now = Date.now() / 1000;
                let best = null;
                this._walk(JSON.parse(agenda), o => {
                    const s = this._ts(o.start_time ?? o.start);
                    if (typeof (o.summary ?? o.title) === 'string' && s && s > now - 60 && (!best || s < best))
                        best = s;
                });
                if (best) {
                    const t = this._hm(GLib.DateTime.new_from_unix_local(Math.floor(best)));
                    out.push(['x-office-calendar-symbolic', `Next meeting ${t}`]);
                }
            } catch (e) {}
        }

        const focus = this._read(`${cache}/focus.json`);
        if (focus) {
            try {
                const f = JSON.parse(focus);
                const today = GLib.DateTime.new_now_local().format('%Y-%m-%d');
                if (f.date === today && f.sessions)
                    out.push(['alarm-symbolic', `${f.sessions} focus session${f.sessions === 1 ? '' : 's'}`]);
            } catch (e) {}
        }

        const gh = this._read(`${cache}/github.json`);
        if (gh) {
            try {
                const n = JSON.parse(gh).notif;
                if (n)
                    out.push(['mail-unread-symbolic', `${n} GitHub notification${n === 1 ? '' : 's'}`]);
            } catch (e) {}
        }

        const battery = this._battery();
        if (battery)
            out.push(battery);
        return out;
    }
}
