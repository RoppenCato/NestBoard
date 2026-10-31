# NestBoard – handover

## Goal
Turn Robin's Google Nest Hub (the one with a screen) into a simple, useful home info screen that isn't tied to Google's own clutter.

The first version is a **weekday morning screen** showing three things:
1. **Trains Knivsta → Stockholm C**: next departures with delays, cancellations, track and deviation info. Robin commutes by Mälartåg, and **only Mälartåg is shown** (SL pendeltåg is filtered out; setting `PRODUCT`).
2. **Weather in Knivsta**: current conditions plus the next 6 hours.
3. **News headlines**: Omni wanted; SVT used for now.

Longer term it may become the hub for a simplified smart home (see Roadmap).

## About Robin
- Based in Sweden, commutes Knivsta → Stockholm in the mornings.
- Strong SAS/SQL, growing Python. Comfortable running scripts but not a web developer.
- Runs personal projects on his **home PC** with the Claude desktop app. At work he only has claude.ai in the browser, so testing happens in the evenings at home.
- Prefers terse, direct answers and incremental changes when debugging. Don't rewrite everything at once.
- Code-change style: comment out old code when a whole block is rewritten, or just the line when only order or wording changes. Add a comment above each change, like `# Claude: Changed so that ... because ... now it works like ...`.

## Home setup (relevant devices)
- **Google Nest Hub** (screen). Main target for NestBoard.
- **Philips Hue** lamps with a Bridge. The Hue and Google Home apps feel messy, and Robin wants it simpler.
- **Apple TV**. He wants to say e.g. "Hey Google, Netflix" to start the Apple TV with that app. This needs Home Assistant, since Google Home has no Apple TV support.
- **PS5**, **Switch 2**, and a **Samsung S24 Ultra** phone.
- **Home PC** (gaming PC) that runs the script for now.

## Current state (v0, 2026-10-26)
- `morgonskarm.py` exists: a single Python file, standard library only, plus the `catt` CLI. **It has not been run yet.** Robin will do the first run at home.
- What it does:
  - Runs a local web server on port 8765.
  - `/` serves a dark, Nest-Hub-sized page (1024×600).
  - `/data` returns JSON with trains, weather and news, cached for 50 seconds.
  - The page polls `/data` every 60 seconds.
  - A background loop runs `catt -d <NEST_NAME> cast_site http://<pc-ip>:8765` on weekdays 06–09, re-casts every 15 minutes, and stops casting after 09.
- Settings are at the top of the file: `TRV_KEY`, `FROM_STATION`, `TO_STATION`, `NEST_NAME`, `NEWS_FEED`, coordinates, port and time window.

### Data sources
- **Trains: Trafikverket Öppet API**
  - POST XML to `https://api.trafikinfo.trafikverket.se/v2/data.json`.
  - Station signatures are looked up via `TrainStation` (no hard-coding).
  - It runs two `TrainAnnouncement` queries: departures (`Avgang`) from Knivsta, and arrivals (`Ankomst`) at Stockholm C. They're matched on `AdvertisedTrainIdent`, so only trains that actually run Knivsta → Stockholm C are shown.
  - Departed trains are skipped (`TimeAtLocation` set). Up to 5 are shown.
- **Weather: Open-Meteo**. Free, no key needed. It was chosen over SMHI's point-forecast API because that API was being replaced and was uncertain. WMO weather codes are mapped to an emoji plus Swedish text.
- **News: RSS** via `NEWS_FEED`. It defaults to SVT (`https://www.svt.se/nyheter/rss.xml`). Omni has no official API. A forum post claims an Omni RSS feed exists, but this is unverified.

### API key
Robin needs a free key from Trafikverket's own registration ("Trafikverkets öppna API för trafikinformation" on trafikverket.se), created on the profile page after logging in. Trafiklab's docs are inconsistent: some pages say Trafiklab members can get the key there, while Trafiklab support says to use Trafikverket's portal.

## Update 2026-10-26 (first run done)
- Runs at logon via Task Scheduler task **"NestBoard"** (`pythonw.exe morgonskarm.py`, no time limit, restarts on crash).
- Key in user env var `TRV_KEY`. Port is **8766** (8765 is used by the Game Developer project). Hub is named **"Lilla rara"** (192.168.0.38).
- ~~Cast window and train filter: weekdays 06:00–09:30~~ → now: **cast weekdays 06–23, weekends/holidays 07–24**. Trains (Mälartåg only) are shown only **06:00–09:30 on workdays**; otherwise the train card is hidden and news/weather get bigger. Screen dims from 21:00.
- Per-source refresh: trains 50 s (only inside train window), news 5 min, weather 15 min.
- Tap any part (trains, weather, news, date line) for a big view; tap again or wait 90 s to go back. **Touch on the cast Hub is not yet confirmed working.**
- News: SVT national + SVT Uppsala. Header: date, week, name days, holiday/flag day (sholiday.faboul.se).
- Fixed: `TrainStation` schema 1.4, `truststore` for Trafikverket SSL, catt run via `python -m catt.cli`.

- Spoken train alerts on the Hub (`alert_loop`, `speak`, `new_alerts`): 07:00-09:00 on workdays, for trains departing 07:00-09:00 and within 60 min (usual trains are 07:50 and 08:20), on cancellation or a delay of 5+ min. Repeated only if cancelled or 5+ min later than last said. Voice via `edge-tts` (text goes to Microsoft), played with `catt cast`, which blocks until the clip ends; the screen is then re-cast. "Already said" is kept in memory, so a script restart can repeat an alert. Settings: `ALERTS_ON`, `ALERT_FROM`, `ALERT_TO`, `ALERT_MIN_DELAY`, `ALERT_AHEAD_MIN`, `ALERT_VOICE`.

## Unverified / likely first-run issues
- `schemaversion` values: `TrainAnnouncement` 1.9 and `TrainStation` 1.5. If Trafikverket rejects them, check the current versions in their data model docs.
- The `TrainStation` lookup filters on `AdvertisedLocationName` = "Knivsta" / "Stockholm C". Check the exact names if the lookup fails.
- Requires Python 3.11 or newer, because `datetime.fromisoformat` must handle Trafikverket's `.000+01:00` timestamps.
- Casting:
  - The Hub's exact name comes from `catt scan`.
  - Windows Firewall must allow the port on private networks.
  - Casted pages can be interrupted by voice or music use, which is why the script re-casts.
  - Touch on casted pages is unreliable.
- The PC must be on in the mornings. Autostart is via Windows Task Scheduler with an "At log on" trigger.

## Roadmap / ideas (not started)
1. **Get v0 running**. Fix first-run errors, then tune the layout on the real Hub.
2. Show something useful outside 06–09: clock, photo frame, or an evening view such as tomorrow's weather and the electricity price.
3. **Electricity price** (Swedish hourly spot price, Knivsta is in area SE3) on the screen and possibly as a Hue colour.
4. ~~Move hosting off the gaming PC~~: dropped. NestBoard stays on the home PC (no Raspberry Pi).
5. **Home Assistant** as the hub:
   - Cast its dashboard to the Hub.
   - Apple TV app launching (e.g. Netflix) exposed to Google voice.
   - Hue scenes.
   - PS5 power via ps5-mqtt.
   - "Gaming night" and "Movie" one-tap scenes.
6. **Cleanup of Hue and Google Home**:
   - consistent room and lamp names
   - 3–4 scenes per room
   - delete Hue's default scenes
   - Google Home favourites only
   - routines instead of scattered scenes

## Files
- `morgonskarm.py`: the v0 script. Upload it together with this handover.
