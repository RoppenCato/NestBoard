"""
Morgonskärm för Nest Hub: tåg Knivsta -> Stockholm C, väder och nyheter.

Krav:  Python 3.11+  och  pip install catt truststore edge-tts
       Trafikverket-nyckeln i miljövariabeln TRV_KEY (setx TRV_KEY "...")
Kör:   python morgonskarm.py
Test:  öppna http://localhost:8766 i webbläsaren på datorn
"""
import email.utils, json, os, re, socket, subprocess, sys, tempfile, threading, time, datetime as dt
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# Claude: Added truststore because Trafikverket's server sends an incomplete certificate chain that Python can't verify on its own; now it works like: Python uses Windows' certificate handling, same as the browser.
import truststore
truststore.inject_into_ssl()

# ===================== INSTÄLLNINGAR =====================
# Claude: Changed so that the key is read from the TRV_KEY environment variable because the repo is public on GitHub; now it works like: set TRV_KEY once in Windows and the key never lands in git.
# TRV_KEY      = "KLISTRA_IN_DIN_TRAFIKVERKET_NYCKEL"
TRV_KEY      = os.environ.get("TRV_KEY", "KLISTRA_IN_DIN_TRAFIKVERKET_NYCKEL")
FROM_STATION = "Knivsta"
TO_STATION   = "Stockholm C"
# Claude: Added so that only this train product is shown.
PRODUCT      = "Mälartåg"
# Claude: Changed so that the name matches the Hub because catt scan found it as "Lilla rara" (192.168.0.38).
# NEST_NAME    = "Nest Hub"          # exakt namn från:  catt scan
NEST_NAME    = "Lilla rara"        # exakt namn från:  catt scan
# Claude: Changed so that news mixes SVT national (4 on the main view) with SVT Uppsala (2) because local news covers Knivsta; now it works like: (address, label, on main view, in big view).
# NEWS_FEED    = "https://www.svt.se/nyheter/rss.xml"   # byt till Omnis RSS-adress om du hittar en
NEWS_FEEDS   = [("https://www.svt.se/nyheter/rss.xml", "SVT", 4, 5),
                ("https://www.svt.se/nyheter/lokalt/uppsala/rss.xml", "Uppsala", 2, 3)]
LAT, LON     = 59.726, 17.789      # Knivsta
# Claude: Changed so that the port is 8766 because the Game Developer project's http.server already uses 8765; now it works like: no clash, localhost:8766 is always NestBoard.
# PORT         = 8765
PORT         = 8766
# Claude: Changed so that the screen is cast all day (weekdays 06–23, weekends and holidays 07–24) while trains are still only shown 06:00–09:30 on workdays; now it works like: separate cast window and train window.
# CAST_DAYS    = {0, 1, 2, 3, 4}     # mån–fre
# CAST_FROM, CAST_TO = 6, 9          # visa på Nest Hub 06:00–09:00
# CAST_FROM, CAST_TO = dt.time(6, 0), dt.time(9, 30)   # visa på Nest Hub 06:00–09:30
# SKIP_DAYS_OFF = True
CAST_WORKDAY = (dt.time(6, 0), dt.time(23, 0))   # mån–fre: visa på Nest Hub 06–23
CAST_DAY_OFF = (dt.time(7, 0), dt.time.max)      # helg och helgdagar (julafton m.m.): 07–24
TRAIN_FROM, TRAIN_TO = dt.time(6, 0), dt.time(9, 30)   # tåg visas bara då, och bara på arbetsdagar
DIM_FROM     = 21                  # skärmen dimmas från kl 21
# Claude: Added so that each source is fetched only as often as it changes (seconds).
REFRESH      = {"trains": 50, "news": 300, "weather": 900, "day": 50}
# Claude: Added settings for spoken train alerts on the Hub.
ALERTS_ON    = True
ALERT_MIN_DELAY = 5                # säg till vid minst så här många minuters försening
ALERT_AHEAD_MIN = 60               # bara tåg som avgår inom så här många minuter
# Claude: Added because the usual trains are 07:50 and 08:20; now it works like: the Hub only speaks during these times, and only about trains departing during these times.
ALERT_FROM, ALERT_TO = dt.time(7, 0), dt.time(9, 0)
ALERT_VOICE  = "sv-SE-SofieNeural"
RECAST_MIN   = 15                  # casta om var 15:e min ifall den tappat sidan
MAX_TRAINS   = 5
# =========================================================

TRV_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"


# Claude: Added log() so that messages end up in nestboard.log (pythonw has no window to print to).
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nestboard.log")
def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def http(url, data=None, headers=None):
    h = {"User-Agent": "morgonskarm/1.0"}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=15) as r:
        return r.read()


def trv(queries):
    body = f'<REQUEST><LOGIN authenticationkey="{TRV_KEY}"/>{queries}</REQUEST>'
    raw = http(TRV_URL, body.encode("utf-8"), {"Content-Type": "text/xml"})
    return json.loads(raw)["RESPONSE"]["RESULT"]


_sig = {}
def signature(name):
    """Slår upp stationens signatur (t.ex. Knivsta -> Kn) så den inte behöver hårdkodas."""
    if name not in _sig:
        # Claude: Changed so that schemaversion is 1.4 because 1.5 rejects LocationSignature ("Invalid query attribute"); now it works like: Knivsta -> Kn.
        # q = ('<QUERY objecttype="TrainStation" schemaversion="1.5">'
        q = ('<QUERY objecttype="TrainStation" schemaversion="1.4">'
             f'<FILTER><EQ name="AdvertisedLocationName" value="{name}"/></FILTER>'
             '<INCLUDE>LocationSignature</INCLUDE></QUERY>')
        _sig[name] = trv(q)[0]["TrainStation"][0]["LocationSignature"]
    return _sig[name]


def iso(s):
    return dt.datetime.fromisoformat(s) if s else None


def trains():
    a, b = signature(FROM_STATION), signature(TO_STATION)
    fields = ["AdvertisedTrainIdent", "AdvertisedTimeAtLocation", "EstimatedTimeAtLocation",
              "TimeAtLocation", "Canceled", "TrackAtLocation", "ProductInformation", "Deviation",
              "OtherInformation"]   # Claude: Added OtherInformation for the big train view (e.g. ticket info).
    inc = "".join(f"<INCLUDE>{f}</INCLUDE>" for f in fields)

    def q(sig, activity, start, end):
        return ('<QUERY objecttype="TrainAnnouncement" schemaversion="1.9" orderby="AdvertisedTimeAtLocation">'
                f'<FILTER><AND><EQ name="ActivityType" value="{activity}"/>'
                f'<EQ name="LocationSignature" value="{sig}"/><EQ name="Advertised" value="true"/>'
                f'<GT name="AdvertisedTimeAtLocation" value="$dateadd({start})"/>'
                f'<LT name="AdvertisedTimeAtLocation" value="$dateadd({end})"/></AND></FILTER>{inc}</QUERY>')

    # Avgångar från Knivsta + ankomster till Stockholm C; matchas på tågnummer
    # så att bara tåg som faktiskt går Knivsta -> Stockholm C visas.
    # Claude: Changed so that the query looks 3 h 40 min ahead instead of 2 h because the big view shows the whole morning (at 06:00 that reaches past 09:30).
    # res = trv(q(a, "Avgang", "-00:20:00", "02:00:00") + q(b, "Ankomst", "-00:20:00", "03:30:00"))
    res = trv(q(a, "Avgang", "-00:20:00", "03:40:00") + q(b, "Ankomst", "-00:20:00", "05:00:00"))
    deps = res[0].get("TrainAnnouncement", [])
    arrs = {t["AdvertisedTrainIdent"]: t for t in res[1].get("TrainAnnouncement", [])}

    out = []
    for t in deps:
        # Claude: Added so that only Mälartåg is shown because Robin only takes Mälartåg; now it works like: any other product (e.g. SL Pendeltåg) is skipped.
        if not any(p.get("Description") == PRODUCT for p in t.get("ProductInformation", [])):
            continue
        x = arrs.get(t["AdvertisedTrainIdent"])
        if not x or x["AdvertisedTimeAtLocation"] <= t["AdvertisedTimeAtLocation"]:
            continue                      # går inte mot Stockholm C
        if t.get("TimeAtLocation"):
            continue                      # har redan gått
        adv, est = iso(t["AdvertisedTimeAtLocation"]), iso(t.get("EstimatedTimeAtLocation"))
        # Claude: Added so that only trains departing 06:00–09:30 are shown because later trains aren't needed.
        # Claude: Changed to TRAIN_FROM/TRAIN_TO since the cast window is now separate.
        # if not CAST_FROM <= adv.time() <= CAST_TO:
        if not TRAIN_FROM <= adv.time() <= TRAIN_TO:
            continue
        delay = int((est - adv).total_seconds() // 60) if est else 0
        out.append({
            "time": adv.strftime("%H:%M"),
            "new": est.strftime("%H:%M") if est and delay > 0 else "",
            "delay": max(delay, 0),
            "cancelled": bool(t.get("Canceled")),
            "track": t.get("TrackAtLocation", ""),
            "product": ", ".join(p.get("Description", "") for p in t.get("ProductInformation", [])),
            "arrive": iso(x["AdvertisedTimeAtLocation"]).strftime("%H:%M"),
            "info": " · ".join(d.get("Description", "") for d in t.get("Deviation", [])),
            # Claude: Added for the big view: expected arrival if late, and extra info from Trafikverket.
            "arrive_new": iso(x["EstimatedTimeAtLocation"]).strftime("%H:%M") if x.get("EstimatedTimeAtLocation") else "",
            "other": [o.get("Description", "") for o in t.get("OtherInformation", [])],
        })
        # Claude: Changed so that all morning trains are returned because the big view shows them all; the main view still shows MAX_TRAINS.
        # if len(out) >= MAX_TRAINS:
        #     break
    return out


WMO = {0: ("☀️", "Klart"), 1: ("🌤️", "Mest klart"), 2: ("⛅", "Halvklart"), 3: ("☁️", "Mulet"),
       45: ("🌫️", "Dimma"), 48: ("🌫️", "Dimma"), 51: ("🌦️", "Duggregn"), 53: ("🌦️", "Duggregn"),
       55: ("🌧️", "Duggregn"), 61: ("🌧️", "Lätt regn"), 63: ("🌧️", "Regn"), 65: ("🌧️", "Kraftigt regn"),
       66: ("🌧️", "Underkylt regn"), 67: ("🌧️", "Underkylt regn"), 71: ("🌨️", "Lätt snö"),
       73: ("🌨️", "Snö"), 75: ("❄️", "Kraftig snö"), 77: ("🌨️", "Snökorn"), 80: ("🌦️", "Skurar"),
       81: ("🌧️", "Skurar"), 82: ("⛈️", "Kraftiga skurar"), 85: ("🌨️", "Snöbyar"),
       86: ("❄️", "Snöbyar"), 95: ("⛈️", "Åska"), 96: ("⛈️", "Åska"), 99: ("⛈️", "Åska")}


# Claude: Added dress_advice so the big weather view says what to wear, based on feels-like temperature, rain, snow and wind during a time span (e.g. 07–09).
SNOW = {71, 73, 75, 77, 85, 86}
def dress_advice(feels, rain_pct, rain_mm, wind, codes):
    t = min(feels)
    if t <= -10:  base = "Vinterjacka, mössa, vantar och halsduk"
    elif t <= 0:  base = "Vinterjacka, mössa och vantar"
    elif t <= 7:  base = "Varm jacka, gärna mössa"
    elif t <= 13: base = "Jacka eller tjock tröja"
    elif t <= 18: base = "Tunn jacka eller kofta"
    else:         base = "T-shirt räcker"
    extra = []
    if any(c in SNOW for c in codes):
        extra.append("vinterskor")
    elif max(rain_pct) >= 50 or sum(rain_mm) >= 0.5:
        extra.append("paraply eller regnjacka")
    if max(wind) >= 8:
        extra.append("blåsigt – vindtätt")
    if -2 <= t <= 2 and (sum(rain_mm) > 0 or any(c in SNOW for c in codes)):
        extra.append("risk för halka")
    return base + (" + " + ", ".join(extra) if extra else "")


def weather():
    # Claude: Changed so that more hourly fields and daily summaries are fetched because the big weather view shows today and tomorrow with dress advice.
    # url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
    #        "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
    #        "&hourly=temperature_2m,precipitation_probability,weather_code"
    #        "&wind_speed_unit=ms&timezone=Europe%2FStockholm&forecast_days=2")
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
           "&hourly=temperature_2m,precipitation_probability,weather_code,apparent_temperature,precipitation,wind_speed_10m"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
           "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
           "&wind_speed_unit=ms&timezone=Europe%2FStockholm&forecast_days=2")
    d = json.loads(http(url))
    days = weather_days(d)
    c = d["current"]
    now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    hours = []
    for t, temp, pp, code in zip(d["hourly"]["time"], d["hourly"]["temperature_2m"],
                                 d["hourly"]["precipitation_probability"], d["hourly"]["weather_code"]):
        if dt.datetime.fromisoformat(t) > now and len(hours) < 6:
            hours.append({"h": t[11:13], "temp": round(temp), "rain": pp, "icon": WMO.get(code, ("", ""))[0]})
    icon, text = WMO.get(c["weather_code"], ("", ""))
    # Claude: Added "days" for the big weather view.
    # return {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
    #         "wind": round(c["wind_speed_10m"]), "icon": icon, "text": text, "hours": hours}
    return {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "wind": round(c["wind_speed_10m"]), "icon": icon, "text": text, "hours": hours, "days": days}


# Claude: Added weather_days: one summary per day (today, tomorrow) with every-3rd-hour forecast and dress advice for the trip in (07–09) and home (16–18).
def weather_days(d):
    h, dl = d["hourly"], d["daily"]
    out = []
    for i, date in enumerate(dl["time"]):
        idx = {int(t[11:13]): j for j, t in enumerate(h["time"]) if t.startswith(date)}
        def span(a, b):
            js = [idx[x] for x in range(a, b + 1) if x in idx]
            return dress_advice([h["apparent_temperature"][j] for j in js],
                                [h["precipitation_probability"][j] or 0 for j in js],
                                [h["precipitation"][j] or 0 for j in js],
                                [h["wind_speed_10m"][j] for j in js],
                                [h["weather_code"][j] for j in js])
        icon, text = WMO.get(dl["weather_code"][i], ("", ""))
        out.append({
            "name": "Idag" if i == 0 else "Imorgon",
            "icon": icon, "text": text,
            "max": round(dl["temperature_2m_max"][i]), "min": round(dl["temperature_2m_min"][i]),
            "rain_mm": dl["precipitation_sum"][i], "rain_pct": dl["precipitation_probability_max"][i],
            "wind": round(dl["wind_speed_10m_max"][i]),
            "sunrise": dl["sunrise"][i][11:16], "sunset": dl["sunset"][i][11:16],
            "dress_am": span(7, 9), "dress_pm": span(16, 18),
            "hours": [{"h": f"{x:02d}", "temp": round(h["temperature_2m"][idx[x]]),
                       "icon": WMO.get(h["weather_code"][idx[x]], ("", ""))[0],
                       "rain": h["precipitation_probability"][idx[x]]} for x in range(6, 24, 3) if x in idx],
        })
    return out


# Claude: Rewrote news() so that it reads several feeds and keeps the summary because the big news view shows it; now it works like: each item has title, summary, source, time and "top" (shown on the main view).
# def news():
#     root = ET.fromstring(http(NEWS_FEED))
#     return [i.findtext("title", "").strip() for i in root.iter("item")][:6]
def news():
    out = []
    for url, label, top, big in NEWS_FEEDS:
        items = []
        for i in ET.fromstring(http(url)).iter("item"):
            title = i.findtext("title", "").strip()
            if not title or title.startswith("Vet du mer"):
                continue                  # SVT lokalt: uppmaningar att tipsa, inte nyheter
            when = i.findtext("pubDate", "")
            try:
                when = email.utils.parsedate_to_datetime(when).astimezone().strftime("%H:%M")
            except Exception:
                when = ""
            # Claude: Added "link" so the story view can show a QR code to the full article.
            items.append({"title": title, "desc": re.sub(r"<[^>]+>", "", i.findtext("description", "")).strip(),
                          "src": label, "time": when, "top": len(items) < top,
                          "link": i.findtext("link", "").strip()})
            if len(items) >= big:
                break
        out += items
    return out


# Claude: Added day_info so that the header shows weekday, week number, name days, holidays and flag days, and so casting can skip days off (e.g. julafton); fetched once per day.
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli",
          "augusti", "september", "oktober", "november", "december"]
_day = {}
def day_info(day=None):
    day = day or dt.date.today()
    if day not in _day:
        raw = http(f"https://sholiday.faboul.se/dagar/v2.1/{day:%Y/%m/%d}").decode("utf-8")
        d = json.loads(raw[raw.index("{"):])["dagar"][0]   # tjänsten skriver ibland PHP-varningar före JSON
        _day.clear()
        _day[day] = {
            "date": f'{d["veckodag"]} {day.day} {MONTHS[day.month - 1]}',
            "week": int(d["vecka"]),
            "names": ", ".join(d.get("namnsdag", [])),
            "holiday": d.get("helgdag", ""),
            "flag": d.get("flaggdag", ""),
            "day_off": d.get("arbetsfri dag") == "Ja",
        }
        # Claude: Added for the big date view: name days for the next 7 days and the next 3 holidays (looks in this month and the next two).
        try:
            cal = []
            for k in range(3):
                y, m = divmod(day.month - 1 + k, 12)
                r = http(f"https://sholiday.faboul.se/dagar/v2.1/{day.year + y}/{m + 1:02d}").decode("utf-8")
                cal += json.loads(r[r.index("{"):])["dagar"]
            future = [c for c in cal if c["datum"] > day.isoformat()]
            def short(c):
                x = dt.date.fromisoformat(c["datum"])
                return f'{c["veckodag"][:3]} {x.day} {MONTHS[x.month - 1][:3]}'
            _day[day]["week_names"] = [{"date": short(c), "names": ", ".join(c.get("namnsdag", []))} for c in future[:7]]
            _day[day]["next_holidays"] = [{"date": short(c), "name": c["helgdag"]} for c in future if c.get("helgdag")][:3]
        except Exception:
            pass
    return _day[day]


# Claude: Added is_workday/train_window: weekday and not a holiday (if the calendar service is down, Mon–Fri counts as workdays).
def is_workday(day=None):
    day = day or dt.date.today()
    if day.weekday() >= 5:
        return False
    try:
        return not day_info(day)["day_off"]
    except Exception:
        return True


def train_window(now=None):
    now = now or dt.datetime.now()
    return is_workday(now.date()) and TRAIN_FROM <= now.time() <= TRAIN_TO


# Claude: Rewrote the cache so that each source has its own refresh time (REFRESH) because the screen now shows all day; now it works like: trains ~every minute, news every 5 min, weather every 15 min, and Trafikverket isn't called at all outside the train window.
# _cache = {"t": 0, "data": None}
# _lock = threading.Lock()
# def get_data():
#     with _lock:
#         if time.time() - _cache["t"] < 50 and _cache["data"]:
#             return _cache["data"]
#         data = {"updated": dt.datetime.now().strftime("%H:%M")}
#         for key, fn in (("trains", trains), ("weather", weather), ("news", news), ("day", day_info)):
#             try:
#                 data[key] = fn()
#             except Exception as e:
#                 data[key + "_error"] = str(e)
#         _cache.update(t=time.time(), data=data)
#         return data
VERSION = str(int(time.time()))   # Claude: Added; changes every time the script starts.
_cache = {}                       # key -> (tid, värde eller None, fel eller None)
_lock = threading.Lock()
def get_data():
    with _lock:
        tw = train_window()
        # Claude: Added "version" so that the page reloads itself when the script has been restarted (the Hub otherwise keeps running an old copy of the page).
        # data = {"updated": dt.datetime.now().strftime("%H:%M"), "train_window": tw}
        data = {"updated": dt.datetime.now().strftime("%H:%M"), "train_window": tw, "version": VERSION}
        for key, fn in (("trains", trains), ("weather", weather), ("news", news), ("day", day_info)):
            if key == "trains" and not tw:
                data["trains"] = []
                continue
            t, val, err = _cache.get(key, (0, None, None))
            if time.time() - t >= REFRESH[key]:
                try:
                    val, err = fn(), None
                except Exception as e:
                    err = str(e)          # behåll senaste lyckade värdet om det finns
                _cache[key] = (time.time(), val, err)
            if val is not None:
                data[key] = val
            elif err:
                data[key + "_error"] = err
        return data


PAGE = """<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Morgon</title>
<style>
*{box-sizing:border-box;margin:0}
body{background:#0e1116;color:#e8eaed;font-family:system-ui,Segoe UI,Roboto,sans-serif;
height:100vh;padding:18px 22px;display:grid;grid-template-rows:auto 1fr auto;gap:14px;overflow:hidden}
header{display:flex;justify-content:space-between;align-items:baseline}
#clock{font-size:44px;font-weight:700}
.muted{color:#8b929b;font-size:15px}
.card{background:#171b22;border-radius:14px;padding:14px 16px}
h2{font-size:15px;color:#8b929b;font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
/* Claude: Changed padding 7px -> 4px and font 24px -> 22px because 5 trains pushed the news off the bottom of the 600px screen. */
/* .train{display:grid;grid-template-columns:80px 70px 1fr 60px;align-items:center;gap:10px;
padding:7px 0;border-bottom:1px solid #232833;font-size:24px} */
.train{display:grid;grid-template-columns:80px 70px 1fr 60px;align-items:center;gap:10px;
padding:4px 0;border-bottom:1px solid #232833;font-size:22px}
.train:last-child{border:0}
.t{font-weight:700}.late{color:#f4b400}.cx{color:#ff5c5c}.cx .t{text-decoration:line-through}
.sub{font-size:14px;color:#8b929b}.track{text-align:right;color:#8b929b;font-size:18px}
.info{grid-column:1/-1;font-size:14px;color:#f4b400;margin-top:-4px}
.bottom{display:grid;grid-template-columns:1fr 1.4fr;gap:14px}
#now{display:flex;align-items:center;gap:12px;font-size:40px;font-weight:700}
#hours{display:flex;gap:14px;margin-top:8px;font-size:15px;color:#c4c8ce}
#hours div{text-align:center}
#news li{list-style:none;font-size:16px;padding:4px 0;border-bottom:1px solid #232833;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#news li:last-child{border:0}
.err{color:#ff5c5c;font-size:14px}
#day{font-size:18px;color:#c4c8ce}#day b{color:#e8eaed}.hol{color:#f4b400}
/* Claude: Added styles for the big view (#detail) that opens when a part is tapped. */
[data-v]{cursor:pointer}
/* Claude: Added .notrains (train card hidden outside 06:00–09:30 on workdays, weather and news take the space) and .dim (darker screen from 21:00). */
body.notrains{grid-template-rows:auto 1fr}
.notrains section[data-v=trains]{display:none}
/* Claude: Changed so that news is easier to read when the trains are hidden: bigger text, wraps to max 2 lines instead of "…", and a wider news column. */
/* .notrains #news li{font-size:19px;padding:7px 0} */
.notrains #news li{font-size:21px;line-height:1.3;padding:8px 0;white-space:normal;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.notrains #news{overflow:hidden;max-height:440px}
.bottom>div{min-width:0}
/* .notrains .bottom{grid-template-columns:1fr 1.25fr} */
.notrains .bottom{grid-template-columns:1fr 1.6fr}.notrains #now{font-size:56px}.notrains #hours{font-size:18px;gap:20px}
.wtom{margin-top:14px;font-size:16px;color:#c4c8ce;line-height:1.6;border-top:1px solid #232833;padding-top:10px}
body.dim{filter:brightness(.45)}
.src{display:inline-block;font-size:11px;font-weight:700;color:#0e1116;background:#8ab4f8;border-radius:4px;padding:1px 5px;margin-right:6px;vertical-align:2px}
#detail{display:none;position:fixed;inset:0;background:#0e1116;padding:18px 22px;overflow:hidden;cursor:pointer}
#detail .top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}
#detail h1{font-size:26px}
/* Claude: Added the big back button at the bottom of every big view, and styles for tappable news and the single story view. */
#detail{padding-bottom:86px}
.back{position:absolute;left:22px;right:22px;bottom:14px;height:60px;border-radius:14px;background:#232833;
display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:700;color:#e8eaed}
.n{cursor:pointer;border-radius:8px;min-width:0}
.art{display:grid;grid-template-columns:1fr auto;gap:28px;align-items:start}
.art h1{font-size:34px!important;line-height:1.25;margin:8px 0 16px}
.art .txt{font-size:24px;line-height:1.5;color:#d6d9de}
.qr{background:#fff;padding:10px;border-radius:10px}
.qrt{font-size:14px;color:#8b929b;text-align:center;margin-top:6px}
.big .train{font-size:22px;padding:6px 0}
.other{grid-column:1/-1;font-size:13px;color:#8b929b;margin-top:-4px}
.wdays{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.wd h3{font-size:24px;margin-bottom:4px}.wd .bigt{font-size:56px;font-weight:700}
.wd .row{color:#c4c8ce;font-size:17px;margin:6px 0 14px}
.wd .hrs{display:flex;justify-content:space-between;font-size:20px;color:#c4c8ce;text-align:center;margin-bottom:16px;line-height:1.35}
.dress{background:#232833;border-radius:10px;padding:12px 14px;font-size:19px;line-height:1.6}
.ncols{display:grid;grid-template-columns:1fr 1fr;gap:6px 18px}
/* Claude: Changed so that headlines in the news list are bigger (16px -> 20px) and the summary is one line, since tapping a story now opens it big. */
/* .n{padding:5px 0;border-bottom:1px solid #232833}.n b{font-size:16px}
.n p{font-size:13px;color:#a9afb7;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;margin-top:2px} */
.n{padding:6px 0;border-bottom:1px solid #232833}.n b{font-size:20px;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.n p{font-size:15px;color:#a9afb7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.dgrid{display:grid;grid-template-columns:1.2fr 1fr;gap:14px}
.dl div{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #232833;font-size:18px}
.dl span:first-child{color:#8b929b}
</style>
<!-- Claude: Added a small QR code library for the story view (scan to read the whole article on the phone). If it can't load, the story is shown without QR. -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
</head><body>
<!-- Claude: Added the #day line next to the clock for date, week, name days, holiday and flag day. -->
<!-- <header><div id="clock">--:--</div><div class="muted" id="upd"></div></header> -->
<!-- Claude: Added data-v on each part so a tap opens its big view, and the #detail container for that view. -->
<!--
<header><div style="display:flex;align-items:baseline;gap:16px"><div id="clock">--:--</div><div id="day"></div></div><div class="muted" id="upd"></div></header>
<section class="card"><h2>Knivsta → Stockholm C</h2><div id="trains"></div></section>
<section class="bottom">
 <div class="card"><h2>Väder Knivsta</h2><div id="weather"></div></div>
 <div class="card"><h2>Nyheter</h2><ul id="news"></ul></div>
</section>
-->
<header><div style="display:flex;align-items:baseline;gap:16px" data-v="day"><div id="clock">--:--</div><div id="day"></div></div><div class="muted" id="upd"></div></header>
<section class="card" data-v="trains"><h2>Knivsta → Stockholm C</h2><div id="trains"></div></section>
<section class="bottom">
 <div class="card" data-v="weather"><h2>Väder Knivsta</h2><div id="weather"></div></div>
 <div class="card" data-v="news"><h2>Nyheter</h2><ul id="news"></ul></div>
</section>
<div id="detail"></div>
<!-- Claude: Added a separate, old-style (ES5) script that runs first, so that errors in the Hub's browser (even syntax errors in the main script) end up in nestboard.log on the PC, plus the browser version once at start. -->
<script>
function report(m){try{new Image().src="/log?m="+encodeURIComponent(String(m).slice(0,1500));}catch(x){}}
window.onerror=function(m,s,l,c,e){report("onerror: "+m+" @"+l+":"+c+" "+(e&&e.stack||""));};
report("start: "+navigator.userAgent+" "+innerWidth+"x"+innerHeight);
</script>
<script>
const $=id=>document.getElementById(id);
const DIM_FROM=__DIM_FROM__;   // Claude: Added; filled in from DIM_FROM in the Python settings when the page is served.
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function clock(){const d=new Date();$("clock").textContent=d.toLocaleTimeString("sv-SE",{hour:"2-digit",minute:"2-digit"});
 // Claude: Added so the screen dims in the evening and night.
 document.body.classList.toggle("dim",d.getHours()>=DIM_FROM||d.getHours()<6);}
async function load(){
 try{
  const d=await (await fetch("/data")).json();
  // Claude: Added so that the page reloads itself if the script on the PC has been restarted with new code.
  if(window.PV===undefined)window.PV=d.version;else if(d.version!==window.PV){location.reload();return;}
  $("upd").textContent="Uppdaterad "+d.updated;
  // Claude: Added so the header shows e.g. "Måndag 26 oktober · v.44 · Amanda, Rasmus" plus holiday/flag day.
  const y=d.day;
  if(y){$("day").innerHTML=`<b>${esc(y.date)}</b> · v.${esc(y.week)}${y.names?" · 🎉 "+esc(y.names):""}`+
   (y.holiday?` · <span class="hol">${esc(y.holiday)}</span>`:"")+(y.flag&&y.flag!==y.holiday?` · 🇸🇪 ${esc(y.flag)}`:y.flag?" 🇸🇪":"");}
  // Claude: Changed so that the main view shows the first 5 trains (the server now sends the whole morning) and the row is built by trow(), shared with the big view.
  // $("trains").innerHTML=d.trains_error?`<div class="err">${esc(d.trains_error)}</div>`:
  //  (d.trains.length?d.trains.map(t=>`<div class="train ${t.cancelled?"cx":t.delay>0?"late":""}">
  //   <div class="t">${t.time}</div>
  //   <div>${t.cancelled?"Inställt":t.new?t.new:""}</div>
  //   <div class="sub">${esc(t.product)} · framme ${t.arrive}${t.delay>0&&!t.cancelled?" · +"+t.delay+" min":""}</div>
  //   <div class="track">${t.track?"Spår "+esc(t.track):""}</div>
  //   ${t.info?`<div class="info">${esc(t.info)}</div>`:""}</div>`).join(""):'<div class="muted">Inga avgångar närmaste timmarna</div>');
  $("trains").innerHTML=d.trains_error?`<div class="err">${esc(d.trains_error)}</div>`:
   (d.trains.length?d.trains.slice(0,5).map(t=>trow(t,false)).join(""):'<div class="muted">Inga fler Mälartåg 06:00–09:30</div>');
  const w=d.weather;
  $("weather").innerHTML=d.weather_error?`<div class="err">${esc(d.weather_error)}</div>`:
   `<div id="now">${w.icon} ${w.temp}°</div><div class="muted">${w.text} · känns som ${w.feels}° · vind ${w.wind} m/s</div>
    <div id="hours">${w.hours.map(h=>`<div>${h.h}<br>${h.icon}<br>${h.temp}°<br><span class="sub">${h.rain}%</span></div>`).join("")}</div>`;
  // Claude: Changed so that only "top" news is shown on the main view, with an Uppsala tag on local news.
  // $("news").innerHTML=d.news_error?`<li class="err">${esc(d.news_error)}</li>`:d.news.map(n=>`<li>${esc(n)}</li>`).join("");
  // Claude: Added so that outside the train window the train card is hidden, all news are shown and the weather card also shows the day ahead.
  document.body.classList.toggle("notrains",!d.train_window);
  const wd=d.weather&&d.weather.days;
  if(!d.train_window&&wd&&!d.weather_error){
   const x=new Date().getHours()>=16?wd[1]:wd[0];
   if(x)$("weather").innerHTML+=`<div class="wtom"><b>${x.name}</b> ${x.icon} ${x.max}° / ${x.min}° · 💧 ${x.rain_pct}%<br>`+
    `🚶 ${esc(x.name==="Idag"?x.dress_pm:x.dress_am)}</div>`;
  }
  $("news").innerHTML=d.news_error?`<li class="err">${esc(d.news_error)}</li>`:
   d.news.filter(n=>n.top||!d.train_window).map(n=>`<li>${n.src!=="SVT"?`<span class="src">${esc(n.src)}</span>`:""}${esc(n.title)}</li>`).join("");
  // Claude: Added so that an open big view is refreshed with the new data too.
  // Claude: Changed to render() so that the back button and QR code are kept when the data refreshes.
  // D=d; if(V)$("detail").innerHTML=views[V]();
  D=d; if(V)render();
 // Claude: Changed so that the error is also sent to the PC's log (nestboard.log), to see what goes wrong on the Hub.
 // }catch(e){$("upd").textContent="Kunde inte hämta data";}
 }catch(e){$("upd").textContent="Kunde inte hämta data";report("load: "+e+" "+(e&&e.stack||""));}
}

// Claude: Added the big views. Tap a part to open it, tap anywhere to go back; it also goes back by itself after 90 s.
let D=null,V=null,T=null;
function trow(t,big){
 return `<div class="train ${t.cancelled?"cx":t.delay>0?"late":""}">
  <div class="t">${t.time}</div>
  <div>${t.cancelled?"Inställt":t.new?t.new:""}</div>
  <div class="sub">${esc(t.product)} · framme ${big&&t.arrive_new&&t.arrive_new!==t.arrive?`<s>${t.arrive}</s> ${t.arrive_new}`:t.arrive}${t.delay>0&&!t.cancelled?" · +"+t.delay+" min":""}</div>
  <div class="track">${t.track?"Spår "+esc(t.track):""}</div>
  ${t.info?`<div class="info">${esc(t.info)}</div>`:""}
  ${big&&t.other&&t.other.length?`<div class="other">${t.other.map(esc).join(" · ")}</div>`:""}</div>`;
}
// Claude: Changed the hint text since there is now a big back button at the bottom.
// const ttl=(title)=>`<div class="top"><h1>${title}</h1><div class="muted">Tryck för att gå tillbaka</div></div>`;
const ttl=(title)=>`<div class="top"><h1>${title}</h1></div>`;
const views={
 trains:()=>ttl("Mälartåg Knivsta → Stockholm C · 06:00–09:30")+(D.trains_error?`<div class="err">${esc(D.trains_error)}</div>`:
  D.trains.length?`<div class="big">${D.trains.slice(0,8).map(t=>trow(t,true)).join("")}</div>`:'<div class="muted">Inga fler Mälartåg i morgonfönstret.</div>'),
 weather:()=>ttl("Väder Knivsta")+(D.weather_error?`<div class="err">${esc(D.weather_error)}</div>`:
  `<div class="wdays">${D.weather.days.map(w=>`<div class="card wd">
   <h3>${w.name}</h3>
   <div class="bigt">${w.icon} ${w.max}° <span class="muted" style="font-size:22px">/ ${w.min}°</span></div>
   <div class="row">${esc(w.text)} · 💧 ${w.rain_mm} mm (${w.rain_pct}%) · 💨 ${w.wind} m/s · 🌅 ${w.sunrise}–${w.sunset}</div>
   <div class="hrs">${w.hours.map(h=>`<div>${h.h}<br>${h.icon}<br>${h.temp}°<br><span class="sub">${h.rain}%</span></div>`).join("")}</div>
   <div class="dress">🚶 <b>Till tåget</b> (07–09): ${esc(w.dress_am)}<br>🏠 <b>Hemvägen</b> (16–18): ${esc(w.dress_pm)}</div>
  </div>`).join("")}</div>`),
 // Claude: Changed so that each story has data-n (its number) and can be tapped to open it big.
 // news:()=>ttl("Nyheter")+(D.news_error?`<div class="err">${esc(D.news_error)}</div>`:
 //  `<div class="ncols">${D.news.map(n=>`<div class="n"><span class="src">${esc(n.src)}</span><span class="muted">${n.time}</span><br><b>${esc(n.title)}</b><p>${esc(n.desc)}</p></div>`).join("")}</div>`),
 news:()=>ttl("Nyheter <span class='muted'>· tryck på en nyhet för att läsa</span>")+(D.news_error?`<div class="err">${esc(D.news_error)}</div>`:
  `<div class="ncols">${D.news.map((n,i)=>`<div class="n" data-n="${i}"><span class="src">${esc(n.src)}</span><span class="muted">${n.time}</span><b>${esc(n.title)}</b><p>${esc(n.desc)}</p></div>`).join("")}</div>`),
 // Claude: Added the single story view: big headline and summary, and a QR code to read the whole article on the phone.
 article:()=>{const n=A||{};return `<div class="art"><div><span class="src">${esc(n.src)}</span><span class="muted">${esc(n.time)}</span>
  <h1>${esc(n.title)}</h1><div class="txt">${esc(n.desc)}</div></div>
  ${n.link?`<div><div class="qr" id="qr"></div><div class="qrt">Läs hela på mobilen</div></div>`:""}</div>`;},
 day:()=>{const y=D.day||{};return ttl(`${esc(y.date||"")} · vecka ${esc(y.week||"")}`)+
  `<div class="dgrid"><div class="card"><h2>Namnsdagar</h2><div class="dl">
    <div><span>Idag</span><b>${esc(y.names||"–")}</b></div>
    ${(y.week_names||[]).map(x=>`<div><span>${esc(x.date)}</span><span>${esc(x.names||"–")}</span></div>`).join("")}</div></div>
   <div class="card"><h2>Kommande helgdagar</h2><div class="dl">
    ${(y.next_holidays||[]).map(x=>`<div><span>${esc(x.date)}</span><span>${esc(x.name)}</span></div>`).join("")}</div>
    ${y.holiday?`<p class="hol" style="margin-top:12px;font-size:18px">Idag: ${esc(y.holiday)}</p>`:""}
    ${y.flag?`<p style="margin-top:8px;font-size:16px">🇸🇪 Flaggdag: ${esc(y.flag)}</p>`:""}</div></div>`;}
};
// Claude: Rewrote navigation so that news -> story -> back works: tap a story in the list to open it; the back button (or tapping anywhere) goes one step back (story -> list -> main view); 90 s without touch goes back to the main view.
// function showView(v){if(!D)return;V=v;$("detail").innerHTML=views[v]();$("detail").style.display="block";clearTimeout(T);T=setTimeout(hideView,90000);}
// function hideView(){V=null;$("detail").style.display="none";clearTimeout(T);}
// document.querySelectorAll("[data-v]").forEach(e=>e.addEventListener("click",()=>showView(e.dataset.v)));
// $("detail").addEventListener("click",hideView);
let A=null;   // vald nyhet i storyvyn
function render(){
 $("detail").innerHTML=views[V]()+`<div class="back">‹ Tillbaka</div>`;
 const q=$("qr");
 if(q&&A&&A.link&&window.QRCode){try{new QRCode(q,{text:A.link,width:170,height:170});}catch(e){}}
}
function showView(v){if(!D)return;V=v;render();$("detail").style.display="block";clearTimeout(T);T=setTimeout(hideView,90000);}
function hideView(){V=null;A=null;$("detail").style.display="none";clearTimeout(T);}
document.querySelectorAll("[data-v]").forEach(e=>e.addEventListener("click",()=>showView(e.dataset.v)));
$("detail").addEventListener("click",e=>{
 const n=e.target.closest("[data-n]");
 if(V==="news"&&n){A=D.news[+n.dataset.n];showView("article");}
 else if(V==="article")showView("news");
 else hideView();
});

clock();load();setInterval(clock,1000);setInterval(load,60000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data"):
            body, ctype = json.dumps(get_data()).encode(), "application/json"
        # Claude: Added /log so the page can report errors from the Hub; written to nestboard.log next to the script.
        elif self.path.startswith("/log"):
            msg = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("m", [""])[0]
            log(f"{self.client_address[0]} {msg}")
            body, ctype = b"", "text/plain"
        else:
            # Claude: Changed so that DIM_FROM is filled into the page.
            # body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            body, ctype = PAGE.replace("__DIM_FROM__", str(DIM_FROM)).encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


# Claude: Added catt() with a lock so that a spoken alert and the regular re-cast never talk to the Hub at the same time. Output is captured because pythonw has no console.
_cast_lock = threading.RLock()
def catt(*args, timeout=60):
    with _cast_lock:
        r = subprocess.run([sys.executable, "-m", "catt.cli", "-d", NEST_NAME, *args], timeout=timeout,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    if r.returncode:
        raise RuntimeError(f"catt {args[0]}: {r.stderr.strip()[-300:]}")


# Claude: Added speak(): makes a clip of the text, plays it on the Hub and then always brings the screen back. Raises an error if it could not be played.
def speak(text, url):
    path = os.path.join(tempfile.gettempdir(), "nestboard_alert.mp3")
    subprocess.run([sys.executable, "-m", "edge_tts", "--voice", ALERT_VOICE, "--text", text, "--write-media", path],
                   timeout=60, check=True, stdin=subprocess.DEVNULL, capture_output=True)
    with _cast_lock:
        try:
            catt("cast", path)
        finally:
            catt("cast_site", url)
    log(f"sa: {text}")


# Claude: Added new_alerts(): picks what is worth saying and remembers it in "sent", so each train is announced once; again only if it gets cancelled or at least ALERT_MIN_DELAY minutes later than last said.
def new_alerts(train_list, now, sent):
    msgs, updates = [], {}
    # Claude: Added so that nothing is said outside ALERT_FROM–ALERT_TO.
    if not ALERT_FROM <= now.time() <= ALERT_TO:
        return msgs, updates
    for t in train_list:
        h, m = map(int, t["time"].split(":"))
        adv = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if (adv - now).total_seconds() > ALERT_AHEAD_MIN * 60:
            continue
        # Claude: Added so that trains departing outside ALERT_FROM–ALERT_TO are not announced.
        if not ALERT_FROM <= adv.time() <= ALERT_TO:
            continue
        key = f"{now:%Y-%m-%d} {t['time']}"
        last = sent.get(key)
        if t["cancelled"]:
            if last != "cancelled":
                msgs.append(f"Tåget {t['time']} mot Stockholm är inställt.")
                updates[key] = "cancelled"
        elif t["delay"] >= ALERT_MIN_DELAY and last != "cancelled" and (last is None or t["delay"] >= last + ALERT_MIN_DELAY):
            msgs.append(f"Tåget {t['time']} mot Stockholm är {t['delay']} minuter försenat. Ny avgång {t['new']}.")
            updates[key] = t["delay"]
    return msgs, updates


# Claude: Added alert_loop(): checks the trains every minute during the train window. "sent" is updated only after the message has played, so a failed attempt is retried next minute.
def alert_loop(url):
    sent = {}
    while True:
        try:
            if ALERTS_ON and train_window():
                d = get_data()
                now = dt.datetime.now()
                if "trains" in d and _cache.get("trains", (0, None, "fel"))[2] is None:
                    msgs, updates = new_alerts(d["trains"], now, sent)
                    if msgs:
                        speak(" ".join(msgs), url)
                        sent.update(updates)
        except Exception as e:
            log(f"Alert-fel: {e}")
        time.sleep(60)


def cast_loop(url):
    casting, last = False, 0
    while True:
        now = dt.datetime.now()
        # Claude: Changed so that minutes count too (09:30, not just 09).
        # in_window = now.weekday() in CAST_DAYS and CAST_FROM <= now.hour < CAST_TO
        # in_window = now.weekday() in CAST_DAYS and CAST_FROM <= now.time() < CAST_TO
        # Claude: Added so that weekday days off (e.g. julafton, nyårsafton) skip casting; if the calendar service is down it casts as usual.
        # if in_window and SKIP_DAYS_OFF:
        #     try:
        #         in_window = not day_info()["day_off"]
        #     except Exception:
        #         pass
        # Claude: Changed so that workdays cast 06–23 and weekends/holidays 07–24 (holidays now use the weekend schedule instead of being skipped).
        start, end = CAST_WORKDAY if is_workday(now.date()) else CAST_DAY_OFF
        in_window = start <= now.time() <= end
        try:
            if in_window and (not casting or time.time() - last > RECAST_MIN * 60):
                # Claude: Changed so that catt is run via Python because catt.exe isn't on PATH; now it works like: the same Python that runs this script starts catt.
                # subprocess.run(["catt", "-d", NEST_NAME, "cast_site", url], timeout=60)
                # Claude: Changed to catt() (see above).
                # subprocess.run([sys.executable, "-m", "catt.cli", "-d", NEST_NAME, "cast_site", url], timeout=60)
                catt("cast_site", url)
                casting, last = True, time.time()
                print(f"{now:%H:%M} castar till {NEST_NAME}")
                log(f"castar till {NEST_NAME}")   # Claude: Added so casting shows in nestboard.log.
            elif not in_window and casting:
                # Claude: Changed the same way as cast_site above (catt via Python).
                # subprocess.run(["catt", "-d", NEST_NAME, "stop"], timeout=60)
                # Claude: Changed to catt() (see above).
                # subprocess.run([sys.executable, "-m", "catt.cli", "-d", NEST_NAME, "stop"], timeout=60)
                catt("stop")
                casting = False
                print(f"{now:%H:%M} slutade casta")
                log("slutade casta")   # Claude: Added.
        except Exception as e:
            print("Cast-fel:", e)
            log(f"Cast-fel: {e}")   # Claude: Added.
        time.sleep(30)


if __name__ == "__main__":
    url = f"http://{local_ip()}:{PORT}"
    print(f"Morgonskärm kör på {url}  (testa i webbläsaren)")
    threading.Thread(target=cast_loop, args=(url,), daemon=True).start()
    threading.Thread(target=alert_loop, args=(url,), daemon=True).start()   # Claude: Added spoken train alerts.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
