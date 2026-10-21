// Prayer times, computed offline (no network) with the praytimes.org astronomy.
// Pure JS (no gi imports) so it can be tested with node.

export const NAMES = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'];

// Twilight angles (degrees below the horizon) per calculation method; `ishaMinutes`
// means Isha is a fixed time after Maghrib.
export const METHODS = {
    egypt: {fajr: 19.5, isha: 17.5},        // Egyptian General Authority of Survey
    mwl: {fajr: 18, isha: 17},              // Muslim World League
    isna: {fajr: 15, isha: 15},             // Islamic Society of North America
    karachi: {fajr: 18, isha: 18},          // University of Islamic Sciences, Karachi
    makkah: {fajr: 18.5, ishaMinutes: 90},  // Umm al-Qura, Makkah
    gulf: {fajr: 19.5, ishaMinutes: 90},
    kuwait: {fajr: 18, isha: 17.5},
    qatar: {fajr: 18, ishaMinutes: 90},
    singapore: {fajr: 20, isha: 18},
    turkey: {fajr: 18, isha: 17},           // Diyanet
    france: {fajr: 12, isha: 12},           // UOIF
    russia: {fajr: 16, isha: 15},
};

const rad = d => d * Math.PI / 180;
const deg = r => r * 180 / Math.PI;
const fix = (a, b) => {
    a -= b * Math.floor(a / b);
    return a < 0 ? a + b : a;
};

function julian(year, month, day) {
    if (month <= 2) {
        year -= 1;
        month += 12;
    }
    const A = Math.floor(year / 100);
    const B = 2 - A + Math.floor(A / 4);
    return Math.floor(365.25 * (year + 4716)) + Math.floor(30.6001 * (month + 1)) + day + B - 1524.5;
}

function sun(jd) {
    const D = jd - 2451545.0;
    const g = fix(357.529 + 0.98560028 * D, 360);
    const q = fix(280.459 + 0.98564736 * D, 360);
    const L = fix(q + 1.915 * Math.sin(rad(g)) + 0.020 * Math.sin(rad(2 * g)), 360);
    const e = 23.439 - 0.00000036 * D;
    const RA = deg(Math.atan2(Math.cos(rad(e)) * Math.sin(rad(L)), Math.cos(rad(L)))) / 15;
    return {
        decl: deg(Math.asin(Math.sin(rad(e)) * Math.sin(rad(L)))),
        eqt: q / 15 - fix(RA, 24),
    };
}

// Returns {Fajr, Sunrise, Dhuhr, Asr, Maghrib, Isha} as local hours (e.g. 15.85 = 3:51 PM).
// tz is the UTC offset in hours for that date (DST included); asr is 'standard' (Shafi'i,
// Maliki, Hanbali) or 'hanafi'.
export function prayerTimes(year, month, day, lat, lng, tz, method = 'egypt', asrJuristic = 'standard') {
    const m = METHODS[method] ?? METHODS.egypt;
    const asrFactor = asrJuristic === 'hanafi' ? 2 : 1;
    const jd = julian(year, month, day) - lng / (15 * 24);
    const midDay = t => fix(12 - sun(jd + t).eqt, 24);
    const angleTime = (angle, t, ccw) => {
        const {decl} = sun(jd + t);
        const noon = midDay(t);
        const c = (-Math.sin(rad(angle)) - Math.sin(rad(decl)) * Math.sin(rad(lat))) /
            (Math.cos(rad(decl)) * Math.cos(rad(lat)));
        const T = deg(Math.acos(Math.max(-1, Math.min(1, c)))) / 15;
        return noon + (ccw ? -T : T);
    };
    const asr = t => {
        const {decl} = sun(jd + t);
        const angle = -deg(Math.atan(1 / (asrFactor + Math.tan(rad(Math.abs(lat - decl))))));
        return angleTime(angle, t);
    };
    // two passes: the first guesses, the second refines with the sun at that time
    let t = {Fajr: 5, Sunrise: 6, Dhuhr: 12, Asr: 13, Maghrib: 18, Isha: 18};
    for (let i = 0; i < 2; i++) {
        const f = {};
        for (const k in t)
            f[k] = t[k] / 24;
        t = {
            Fajr: angleTime(m.fajr, f.Fajr, true),
            Sunrise: angleTime(0.833, f.Sunrise, true),
            Dhuhr: midDay(f.Dhuhr),
            Asr: asr(f.Asr),
            Maghrib: angleTime(0.833, f.Maghrib),
            Isha: m.ishaMinutes ? 0 : angleTime(m.isha, f.Isha),
        };
    }
    if (m.ishaMinutes)
        t.Isha = t.Maghrib + m.ishaMinutes / 60;
    const out = {};
    for (const k in t)
        out[k] = t[k] + tz - lng / 15;
    return out;
}
