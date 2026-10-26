"""
Morgonskärm för Nest Hub: tåg Knivsta -> Stockholm C, väder och nyheter.

Krav:  Python 3.11+  och  pip install catt truststore
       Trafikverket-nyckeln i miljövariabeln TRV_KEY (setx TRV_KEY "...")
Kör:   python morgonskarm.py
Test:  öppna http://localhost:8766 i webbläsaren på datorn
"""
import json, os, socket, subprocess, sys, threading, time, datetime as dt
import urllib.request, xml.etree.ElementTree as ET
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
NEWS_FEED    = "https://www.svt.se/nyheter/rss.xml"   # byt till Omnis RSS-adress om du hittar en
LAT, LON     = 59.726, 17.789      # Knivsta
# Claude: Changed so that the port is 8766 because the Game Developer project's http.server already uses 8765; now it works like: no clash, localhost:8766 is always NestBoard.
# PORT         = 8765
PORT         = 8766
CAST_DAYS    = {0, 1, 2, 3, 4}     # mån–fre
CAST_FROM, CAST_TO = 6, 9          # visa på Nest Hub 06:00–09:00
RECAST_MIN   = 15                  # casta om var 15:e min ifall den tappat sidan
MAX_TRAINS   = 5
# =========================================================

TRV_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"


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
              "TimeAtLocation", "Canceled", "TrackAtLocation", "ProductInformation", "Deviation"]
    inc = "".join(f"<INCLUDE>{f}</INCLUDE>" for f in fields)

    def q(sig, activity, start, end):
        return ('<QUERY objecttype="TrainAnnouncement" schemaversion="1.9" orderby="AdvertisedTimeAtLocation">'
                f'<FILTER><AND><EQ name="ActivityType" value="{activity}"/>'
                f'<EQ name="LocationSignature" value="{sig}"/><EQ name="Advertised" value="true"/>'
                f'<GT name="AdvertisedTimeAtLocation" value="$dateadd({start})"/>'
                f'<LT name="AdvertisedTimeAtLocation" value="$dateadd({end})"/></AND></FILTER>{inc}</QUERY>')

    # Avgångar från Knivsta + ankomster till Stockholm C; matchas på tågnummer
    # så att bara tåg som faktiskt går Knivsta -> Stockholm C visas.
    res = trv(q(a, "Avgang", "-00:20:00", "02:00:00") + q(b, "Ankomst", "-00:20:00", "03:30:00"))
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
        })
        if len(out) >= MAX_TRAINS:
            break
    return out


WMO = {0: ("☀️", "Klart"), 1: ("🌤️", "Mest klart"), 2: ("⛅", "Halvklart"), 3: ("☁️", "Mulet"),
       45: ("🌫️", "Dimma"), 48: ("🌫️", "Dimma"), 51: ("🌦️", "Duggregn"), 53: ("🌦️", "Duggregn"),
       55: ("🌧️", "Duggregn"), 61: ("🌧️", "Lätt regn"), 63: ("🌧️", "Regn"), 65: ("🌧️", "Kraftigt regn"),
       66: ("🌧️", "Underkylt regn"), 67: ("🌧️", "Underkylt regn"), 71: ("🌨️", "Lätt snö"),
       73: ("🌨️", "Snö"), 75: ("❄️", "Kraftig snö"), 77: ("🌨️", "Snökorn"), 80: ("🌦️", "Skurar"),
       81: ("🌧️", "Skurar"), 82: ("⛈️", "Kraftiga skurar"), 85: ("🌨️", "Snöbyar"),
       86: ("❄️", "Snöbyar"), 95: ("⛈️", "Åska"), 96: ("⛈️", "Åska"), 99: ("⛈️", "Åska")}


def weather():
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
           "&hourly=temperature_2m,precipitation_probability,weather_code"
           "&wind_speed_unit=ms&timezone=Europe%2FStockholm&forecast_days=2")
    d = json.loads(http(url))
    c = d["current"]
    now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    hours = []
    for t, temp, pp, code in zip(d["hourly"]["time"], d["hourly"]["temperature_2m"],
                                 d["hourly"]["precipitation_probability"], d["hourly"]["weather_code"]):
        if dt.datetime.fromisoformat(t) > now and len(hours) < 6:
            hours.append({"h": t[11:13], "temp": round(temp), "rain": pp, "icon": WMO.get(code, ("", ""))[0]})
    icon, text = WMO.get(c["weather_code"], ("", ""))
    return {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "wind": round(c["wind_speed_10m"]), "icon": icon, "text": text, "hours": hours}


def news():
    root = ET.fromstring(http(NEWS_FEED))
    return [i.findtext("title", "").strip() for i in root.iter("item")][:6]


_cache = {"t": 0, "data": None}
_lock = threading.Lock()
def get_data():
    with _lock:
        if time.time() - _cache["t"] < 50 and _cache["data"]:
            return _cache["data"]
        data = {"updated": dt.datetime.now().strftime("%H:%M")}
        for key, fn in (("trains", trains), ("weather", weather), ("news", news)):
            try:
                data[key] = fn()
            except Exception as e:
                data[key + "_error"] = str(e)
        _cache.update(t=time.time(), data=data)
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
.train{display:grid;grid-template-columns:80px 70px 1fr 60px;align-items:center;gap:10px;
padding:7px 0;border-bottom:1px solid #232833;font-size:24px}
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
</style></head><body>
<header><div id="clock">--:--</div><div class="muted" id="upd"></div></header>
<section class="card"><h2>Knivsta → Stockholm C</h2><div id="trains"></div></section>
<section class="bottom">
 <div class="card"><h2>Väder Knivsta</h2><div id="weather"></div></div>
 <div class="card"><h2>Nyheter</h2><ul id="news"></ul></div>
</section>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function clock(){const d=new Date();$("clock").textContent=d.toLocaleTimeString("sv-SE",{hour:"2-digit",minute:"2-digit"});}
async function load(){
 try{
  const d=await (await fetch("/data")).json();
  $("upd").textContent="Uppdaterad "+d.updated;
  $("trains").innerHTML=d.trains_error?`<div class="err">${esc(d.trains_error)}</div>`:
   (d.trains.length?d.trains.map(t=>`<div class="train ${t.cancelled?"cx":t.delay>0?"late":""}">
    <div class="t">${t.time}</div>
    <div>${t.cancelled?"Inställt":t.new?t.new:""}</div>
    <div class="sub">${esc(t.product)} · framme ${t.arrive}${t.delay>0&&!t.cancelled?" · +"+t.delay+" min":""}</div>
    <div class="track">${t.track?"Spår "+esc(t.track):""}</div>
    ${t.info?`<div class="info">${esc(t.info)}</div>`:""}</div>`).join(""):'<div class="muted">Inga avgångar närmaste timmarna</div>');
  const w=d.weather;
  $("weather").innerHTML=d.weather_error?`<div class="err">${esc(d.weather_error)}</div>`:
   `<div id="now">${w.icon} ${w.temp}°</div><div class="muted">${w.text} · känns som ${w.feels}° · vind ${w.wind} m/s</div>
    <div id="hours">${w.hours.map(h=>`<div>${h.h}<br>${h.icon}<br>${h.temp}°<br><span class="sub">${h.rain}%</span></div>`).join("")}</div>`;
  $("news").innerHTML=d.news_error?`<li class="err">${esc(d.news_error)}</li>`:d.news.map(n=>`<li>${esc(n)}</li>`).join("");
 }catch(e){$("upd").textContent="Kunde inte hämta data";}
}
clock();load();setInterval(clock,1000);setInterval(load,60000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data"):
            body, ctype = json.dumps(get_data()).encode(), "application/json"
        else:
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
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


def cast_loop(url):
    casting, last = False, 0
    while True:
        now = dt.datetime.now()
        in_window = now.weekday() in CAST_DAYS and CAST_FROM <= now.hour < CAST_TO
        try:
            if in_window and (not casting or time.time() - last > RECAST_MIN * 60):
                # Claude: Changed so that catt is run via Python because catt.exe isn't on PATH; now it works like: the same Python that runs this script starts catt.
                # subprocess.run(["catt", "-d", NEST_NAME, "cast_site", url], timeout=60)
                subprocess.run([sys.executable, "-m", "catt.cli", "-d", NEST_NAME, "cast_site", url], timeout=60)
                casting, last = True, time.time()
                print(f"{now:%H:%M} castar till {NEST_NAME}")
            elif not in_window and casting:
                # Claude: Changed the same way as cast_site above (catt via Python).
                # subprocess.run(["catt", "-d", NEST_NAME, "stop"], timeout=60)
                subprocess.run([sys.executable, "-m", "catt.cli", "-d", NEST_NAME, "stop"], timeout=60)
                casting = False
                print(f"{now:%H:%M} slutade casta")
        except Exception as e:
            print("Cast-fel:", e)
        time.sleep(30)


if __name__ == "__main__":
    url = f"http://{local_ip()}:{PORT}"
    print(f"Morgonskärm kör på {url}  (testa i webbläsaren)")
    threading.Thread(target=cast_loop, args=(url,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
