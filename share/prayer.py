"""Prayer times and the Hijri date, computed offline.

The astronomy follows praytimes.org (the same as the lock screen's prayer.js). Times are returned
as local hours (15.85 = 3:51 PM). The Hijri date uses the tabular (Kuwaiti) calendar, so it can be
a day away from the sighted date.
"""
import math

NAMES = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]

METHODS = {
    "egypt": {"fajr": 19.5, "isha": 17.5},
    "mwl": {"fajr": 18, "isha": 17},
    "isna": {"fajr": 15, "isha": 15},
    "karachi": {"fajr": 18, "isha": 18},
    "makkah": {"fajr": 18.5, "isha_minutes": 90},
    "gulf": {"fajr": 19.5, "isha_minutes": 90},
    "kuwait": {"fajr": 18, "isha": 17.5},
    "qatar": {"fajr": 18, "isha_minutes": 90},
    "singapore": {"fajr": 20, "isha": 18},
    "turkey": {"fajr": 18, "isha": 17},
    "france": {"fajr": 12, "isha": 12},
    "russia": {"fajr": 16, "isha": 15},
}

_rad = math.radians
_deg = math.degrees


def _fix(a, b):
    a -= b * math.floor(a / b)
    return a + b if a < 0 else a


def _julian(year, month, day):
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5


def _sun(jd):
    d = jd - 2451545.0
    g = _fix(357.529 + 0.98560028 * d, 360)
    q = _fix(280.459 + 0.98564736 * d, 360)
    lam = _fix(q + 1.915 * math.sin(_rad(g)) + 0.020 * math.sin(_rad(2 * g)), 360)
    e = 23.439 - 0.00000036 * d
    ra = _deg(math.atan2(math.cos(_rad(e)) * math.sin(_rad(lam)), math.cos(_rad(lam)))) / 15
    return _deg(math.asin(math.sin(_rad(e)) * math.sin(_rad(lam)))), q / 15 - _fix(ra, 24)


def prayer_times(year, month, day, lat, lng, tz, method="mwl", asr="standard"):
    """{'Fajr','Sunrise','Dhuhr','Asr','Maghrib','Isha'} as local hours; tz = UTC offset in hours."""
    m = METHODS.get(method, METHODS["mwl"])
    asr_factor = 2 if asr == "hanafi" else 1
    jd = _julian(year, month, day) - lng / (15 * 24)

    def mid_day(t):
        return _fix(12 - _sun(jd + t)[1], 24)

    def angle_time(angle, t, ccw=False):
        decl, _eq = _sun(jd + t)
        noon = mid_day(t)
        c = (-math.sin(_rad(angle)) - math.sin(_rad(decl)) * math.sin(_rad(lat))) / \
            (math.cos(_rad(decl)) * math.cos(_rad(lat)))
        hours = _deg(math.acos(max(-1, min(1, c)))) / 15
        return noon - hours if ccw else noon + hours

    def asr_time(t):
        decl, _eq = _sun(jd + t)
        angle = -_deg(math.atan(1 / (asr_factor + math.tan(_rad(abs(lat - decl))))))
        return angle_time(angle, t)

    t = {"Fajr": 5, "Sunrise": 6, "Dhuhr": 12, "Asr": 13, "Maghrib": 18, "Isha": 18}
    for _ in range(2):
        f = {k: v / 24 for k, v in t.items()}
        t = {"Fajr": angle_time(m["fajr"], f["Fajr"], True), "Sunrise": angle_time(0.833, f["Sunrise"], True),
             "Dhuhr": mid_day(f["Dhuhr"]), "Asr": asr_time(f["Asr"]), "Maghrib": angle_time(0.833, f["Maghrib"]),
             "Isha": 0 if "isha_minutes" in m else angle_time(m["isha"], f["Isha"])}
    if "isha_minutes" in m:
        t["Isha"] = t["Maghrib"] + m["isha_minutes"] / 60
    return {k: v + tz - lng / 15 for k, v in t.items()}


HIJRI_MONTHS_EN = ["Muharram", "Safar", "Rabi' I", "Rabi' II", "Jumada I", "Jumada II", "Rajab", "Sha'ban",
                   "Ramadan", "Shawwal", "Dhu al-Qi'da", "Dhu al-Hijja"]
HIJRI_MONTHS_AR = ["محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة", "رجب", "شعبان",
                   "رمضان", "شوال", "ذو القعدة", "ذو الحجة"]


def hijri(year, month, day):
    """(year, month, day) in the tabular Hijri calendar for a Gregorian date."""
    jd = int(_julian(year, month, day) + 0.5)
    l = jd - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = ((10985 - l) // 5316) * ((50 * l) // 17719) + (l // 5670) * ((43 * l) // 15238)
    l = l - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    m = (24 * l) // 709
    d = l - (709 * m) // 24
    y = 30 * n + j - 30
    return y, m, d


def hijri_text(year, month, day, arabic=False):
    y, m, d = hijri(year, month, day)
    if arabic:
        digits = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
        return f"{d} {HIJRI_MONTHS_AR[m - 1]} {y}".translate(digits)
    return f"{d} {HIJRI_MONTHS_EN[m - 1]} {y}"
