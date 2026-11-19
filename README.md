# WiFi Signal Atlas

Map the WiFi signal in your home and see it in 3D: floors stacked in space, walls,
your router, the spots you measured, and a heatmap (or a "signal terrain") of how
strong the WiFi is everywhere in between.


## How it works

1. **Measure.** Walk around with a laptop running `scanner/wifi_survey.py`, or with a
   phone WiFi analyzer app, and note the signal (in dBm) at several spots.
2. **Draw.** Open `web/index.html`, set each floor's size, and draw the walls
   (brick, drywall, glass, metal… each type blocks a different amount of signal).
3. **See it in 3D.** The page fills the gaps between your readings with a multi-wall
   radio model, calibrated to what you actually measured, and shows:
   - a heatmap or 3D signal terrain for every floor,
   - % of each floor with good (≥ −67 dBm), weak and dead coverage,
   - hover readouts ("−72 dBm, from Main router, 7.4 m away through 3 walls"),
   - **Best spot**: where to move the router for the most coverage,
   - **Locate**: estimate where an unknown router (a neighbour's, an extender) is,
     from the readings that saw it.

Drag a router in the 3D view to see coverage change live.

## 1. Record readings with a laptop

No packages needed, Python 3.8+. Linux (NetworkManager/iw), macOS and Windows.

```sh
python3 scanner/wifi_survey.py -o survey.json
```

Pick one corner of each floor as `0,0` and measure in metres. Then at the prompt:

```
survey> floor 0 11 8 Ground floor     # floor 0 is 11 m wide, 8 m deep
survey> ap 1 1.2 0 Main router        # stand next to the router and mark it
survey> p 3.5 2 0 Sofa                # take a reading at x=3.5, y=2 on floor 0
survey> p 9 6 0 Kitchen
survey> p 3 3 1 Bedroom               # floor 1 = upstairs
survey> quit
```

Each reading averages a few scans and records **every** network the laptop hears.
Try it without WiFi hardware: `python3 scanner/wifi_survey.py --demo`.

Notes:
- Windows and NetworkManager report signal as a percentage; the script converts it
  to approximate dBm. On Linux, `iw` is used for the connected network's exact dBm.
- macOS hides router MAC addresses from scripts, so networks are named by SSID and channel.

## 2. Or type readings from your phone

Use any WiFi analyzer app that shows dBm. In the page, pick the **Reading** tool,
click where you stood, and type the number.

## 3. View in 3D

Open `web/index.html` in a browser (double-click works), click **Load survey file**,
and pick your `survey.json`. Or drop the file onto the 3D view. Your plan is kept in
the browser; use **View / paste JSON** to copy a backup.

Controls: drag to orbit, scroll to zoom, right-drag to pan, `Ctrl+Z` to undo.

## What it can and can't do

The map is a model (log-distance path loss plus per-wall and per-floor losses,
COST-231 multi-wall style) pulled toward your real measurements. More readings, spread
around each room, give a truer map. It does not "see" through walls or scan room shapes
by itself; that needs special WiFi sensing hardware (channel state information from
ESP32 or Intel 5300 cards), which ordinary laptops and phones don't expose.

## Files

| Path | What |
| --- | --- |
| `web/index.html` | The 3D app (single file, opens offline once three.js is cached) |
| `web/wifi-atlas.html` | Source of the page body; run `web/build.sh` after editing it |
| `scanner/wifi_survey.py` | Cross-platform signal recorder |
| `scanner/example-survey.json` | Small example produced by `--demo` |
