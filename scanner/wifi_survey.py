#!/usr/bin/env python3
"""
wifi_survey.py - walk around your home and record real WiFi signal strength.

Carry a laptop from spot to spot. At each spot, type where you are (in metres
from one corner of the floor) and the script takes a few WiFi scans, averages
them, and saves the readings. Load the resulting JSON into WiFi Signal Atlas
(web/index.html) to see your home in 3D.

Works on Linux (NetworkManager / iw), macOS and Windows. No extra packages.

    python3 wifi_survey.py                  # writes survey.json
    python3 wifi_survey.py -o upstairs.json --samples 6
    python3 wifi_survey.py --demo           # fake scanner, to try it out

Commands at the prompt:
    p X Y [FLOOR] [LABEL]    take a reading here, e.g.  p 3.5 2 0 Sofa
    ap X Y [FLOOR] [NAME]    mark the router you are connected to at X,Y
    floor N WIDTH DEPTH [NAME]   set a floor's size in metres, e.g. floor 0 11 8 Ground
    scan                     show what the laptop sees right now
    list                     show the readings taken so far
    undo                     remove the last reading
    help                     show this help
    quit                     save and exit (the file is also saved after every reading)
"""
import argparse
import json
import math
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import time

MIN_DBM = -95


def run(cmd, timeout=25):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pct_to_dbm(pct):
    """Windows and NetworkManager report signal quality as 0-100 %.
    The usual mapping is linear between -100 dBm (0 %) and -50 dBm (100 %)."""
    return round(max(0, min(100, pct)) / 2 - 100)


def band_of(freq_mhz=None, channel=None):
    if freq_mhz:
        return "6" if freq_mhz >= 5925 else "5" if freq_mhz >= 4900 else "2.4"
    if channel:
        return "2.4" if channel <= 14 else "5"
    return None


# ---------------------------------------------------------------- Linux
def scan_linux():
    nets = {}
    if shutil.which("nmcli"):
        out = run(["nmcli", "-t", "-f", "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL",
                   "device", "wifi", "list", "--rescan", "yes"])
        if not out:
            out = run(["nmcli", "-t", "-f", "IN-USE,BSSID,SSID,CHAN,FREQ,SIGNAL",
                       "device", "wifi", "list"])
        for line in out.splitlines():
            parts = [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line)]
            if len(parts) < 6:
                continue
            inuse, bssid, ssid, chan, freq, sig = parts[:6]
            try:
                freq_mhz = int(re.sub(r"\D", "", freq) or 0)
                nets[bssid.lower()] = {
                    "ssid": ssid, "channel": int(chan or 0) or None,
                    "band": band_of(freq_mhz), "rssi": pct_to_dbm(int(sig)),
                    "connected": inuse.strip() == "*",
                }
            except ValueError:
                continue
    # iw gives the connected link in real dBm, which is more precise than nmcli's %.
    if shutil.which("iw"):
        for iface in re.findall(r"Interface\s+(\S+)", run(["iw", "dev"])):
            link = run(["iw", "dev", iface, "link"])
            m_b = re.search(r"Connected to ([0-9a-fA-F:]{17})", link)
            m_s = re.search(r"signal:\s*(-?\d+)\s*dBm", link)
            if m_b and m_s:
                b = m_b.group(1).lower()
                ssid = (re.search(r"SSID:\s*(.+)", link) or [None, ""])[1].strip()
                freq = re.search(r"freq:\s*(\d+)", link)
                entry = nets.get(b, {"ssid": ssid, "channel": None,
                                     "band": band_of(int(freq.group(1))) if freq else None})
                entry.update(rssi=int(m_s.group(1)), connected=True)
                nets[b] = entry
    return nets


# ---------------------------------------------------------------- macOS
AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"


def scan_macos():
    nets = {}
    raw = run(["system_profiler", "SPAirPortDataType", "-json"], timeout=40)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}

    def add(info, connected):
        sn = info.get("spairport_signal_noise", "")
        m = re.search(r"(-?\d+)\s*dBm", sn)
        if not m:
            return
        ssid = info.get("_name", "?")
        chan_txt = str(info.get("spairport_network_channel", ""))
        chan = int(re.match(r"\d+", chan_txt).group()) if re.match(r"\d+", chan_txt) else None
        band = "6" if "6GHz" in chan_txt else "5" if "5GHz" in chan_txt else band_of(channel=chan)
        # macOS hides BSSIDs from command-line tools, so name networks by SSID + channel.
        key = f"{ssid}@ch{chan}" if chan else ssid
        nets[key.lower()] = {"ssid": ssid, "channel": chan, "band": band,
                             "rssi": int(m.group(1)), "connected": connected}

    for block in data.get("SPAirPortDataType", []):
        for iface in block.get("spairport_airport_interfaces", []):
            cur = iface.get("spairport_current_network_information")
            if cur:
                add(cur, True)
            for other in iface.get("spairport_airport_other_local_wireless_networks", []):
                add(other, False)
    if not nets and os.path.exists(AIRPORT):  # older macOS
        info = run([AIRPORT, "-I"])
        rssi = re.search(r"agrCtlRSSI:\s*(-?\d+)", info)
        ssid = re.search(r"\bSSID:\s*(.+)", info)
        bssid = re.search(r"BSSID:\s*([0-9a-fA-F:]+)", info)
        if rssi and ssid:
            nets[(bssid.group(1) if bssid else ssid.group(1)).lower()] = {
                "ssid": ssid.group(1).strip(), "channel": None, "band": None,
                "rssi": int(rssi.group(1)), "connected": True}
    return nets


# ---------------------------------------------------------------- Windows
def scan_windows():
    nets = {}
    out = run(["netsh", "wlan", "show", "networks", "mode=bssid"])
    ssid = None
    cur = None
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"^SSID \d+\s*:\s*(.*)$", line)
        if m:
            ssid = m.group(1)
            continue
        m = re.match(r"^BSSID \d+\s*:\s*([0-9a-fA-F:]{17})", line)
        if m:
            cur = m.group(1).lower()
            nets[cur] = {"ssid": ssid, "channel": None, "band": None, "rssi": None, "connected": False}
            continue
        if cur:
            m = re.match(r"^Signal\s*:\s*(\d+)%", line)
            if m:
                nets[cur]["rssi"] = pct_to_dbm(int(m.group(1)))
            m = re.match(r"^Channel\s*:\s*(\d+)", line)
            if m:
                nets[cur]["channel"] = int(m.group(1))
                nets[cur]["band"] = band_of(channel=int(m.group(1)))
    info = run(["netsh", "wlan", "show", "interfaces"])
    b = re.search(r"BSSID\s*:\s*([0-9a-fA-F:]{17})", info)
    if b:
        key = b.group(1).lower()
        entry = nets.setdefault(key, {"ssid": None, "channel": None, "band": None, "rssi": None})
        entry["connected"] = True
        s = re.search(r"^\s*SSID\s*:\s*(.+)$", info, re.M)
        if s and not entry.get("ssid"):
            entry["ssid"] = s.group(1).strip()
        r = re.search(r"Rssi\s*:\s*(-?\d+)", info)  # newer Windows builds
        p = re.search(r"Signal\s*:\s*(\d+)%", info)
        if r:
            entry["rssi"] = int(r.group(1))
        elif p:
            entry["rssi"] = pct_to_dbm(int(p.group(1)))
    return {k: v for k, v in nets.items() if v.get("rssi") is not None}


# ---------------------------------------------------------------- demo
class DemoScanner:
    """Pretends to be a laptop in an 11 x 8 m home with a router at (1, 1.2)."""
    APS = {"a4:2b:b0:11:22:33": ("HomeNet", 6, 1.0, 1.2, 0, -38),
           "f0:9f:c2:aa:bb:cc": ("Neighbour-5G", 44, 10.5, 7.5, 1, -45)}

    def __init__(self):
        self.pos = (5.0, 4.0, 0)

    def __call__(self):
        x, y, f = self.pos
        nets = {}
        for bssid, (ssid, ch, ax, ay, af, p0) in self.APS.items():
            d = max(0.5, math.dist((x, y, f * 2.7), (ax, ay, af * 2.7)))
            rssi = p0 - 22 * math.log10(d) - 13 * abs(f - af) + random.gauss(0, 2)
            if rssi > MIN_DBM:
                nets[bssid] = {"ssid": ssid, "channel": ch, "band": band_of(channel=ch),
                               "rssi": round(rssi), "connected": ssid == "HomeNet"}
        return nets


def pick_scanner(demo):
    if demo:
        return DemoScanner()
    system = platform.system()
    if system == "Linux":
        return scan_linux
    if system == "Darwin":
        return scan_macos
    if system == "Windows":
        return scan_windows
    sys.exit(f"Unsupported system: {system}")


# ---------------------------------------------------------------- survey file
def load_survey(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        print(f"Continuing {path}: {len(data.get('samples', []))} readings so far.")
        return data
    return {"version": 1, "name": "My home", "floorHeight": 2.7,
            "model": {"band": "2.4", "n": 2.0},
            "floors": [], "walls": [], "aps": [], "networks": {}, "samples": []}


def save_survey(path, survey):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(survey, fh, indent=2)
    os.replace(tmp, path)


def ensure_floor(survey, f):
    while len(survey["floors"]) <= f:
        n = len(survey["floors"])
        survey["floors"].append({"name": "Ground floor" if n == 0 else f"Floor {n + 1}",
                                 "width": 10, "depth": 8})


def grow_floor(survey, f, x, y):
    ensure_floor(survey, f)
    fl = survey["floors"][f]
    fl["width"] = max(fl["width"], math.ceil(x + 0.5))
    fl["depth"] = max(fl["depth"], math.ceil(y + 0.5))


def take_reading(scanner, samples, interval):
    seen = {}
    meta = {}
    for i in range(samples):
        nets = scanner()
        for key, n in nets.items():
            seen.setdefault(key, []).append(n["rssi"])
            meta[key] = n
        print(f"  scan {i + 1}/{samples}: {len(nets)} networks", end="\r", flush=True)
        if i < samples - 1:
            time.sleep(interval)
    print(" " * 40, end="\r")
    readings = {k: round(statistics.mean(v)) for k, v in seen.items()
                if statistics.mean(v) > MIN_DBM}
    return readings, meta


def describe(readings, meta, limit=6):
    rows = sorted(readings.items(), key=lambda kv: -kv[1])[:limit]
    for key, rssi in rows:
        n = meta.get(key, {})
        tag = "  <- connected" if n.get("connected") else ""
        print(f"    {rssi:>4} dBm  {n.get('ssid') or '?':<24} {key}{tag}")


def parse_xy(args, usage):
    if len(args) < 2:
        raise ValueError(usage)
    x, y = float(args[0]), float(args[1])
    f = int(args[2]) if len(args) > 2 and re.fullmatch(r"\d+", args[2]) else 0
    rest = args[3:] if len(args) > 2 and re.fullmatch(r"\d+", args[2]) else args[2:]
    return x, y, f, " ".join(rest)


def main():
    ap = argparse.ArgumentParser(description="Record WiFi signal strength around your home.")
    ap.add_argument("-o", "--output", default="survey.json", help="survey file to write (default survey.json)")
    ap.add_argument("--samples", type=int, default=4, help="scans to average at each spot (default 4)")
    ap.add_argument("--interval", type=float, default=1.5, help="seconds between scans (default 1.5)")
    ap.add_argument("--name", help="name of the place, e.g. 'Flat 3B'")
    ap.add_argument("--demo", action="store_true", help="use a fake scanner to try the workflow")
    args = ap.parse_args()

    scanner = pick_scanner(args.demo)
    survey = load_survey(args.output)
    if args.name:
        survey["name"] = args.name

    print(__doc__.split("Commands at the prompt:")[1].rstrip())
    print("\nTip: stand still, hold the laptop at desk height, and pick one corner of each")
    print("floor as 0,0. X runs along one wall, Y along the other, in metres.\n")

    first = scanner()
    if not first:
        print("No WiFi networks found. Is WiFi switched on? On Linux, is NetworkManager running?")
    else:
        print(f"Scanner works: {len(first)} networks visible.\n")

    while True:
        try:
            line = input("survey> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        cmd, *rest = line.split()
        cmd = cmd.lower()
        try:
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd in ("h", "help", "?"):
                print(__doc__.split("Commands at the prompt:")[1].rstrip())
            elif cmd == "scan":
                nets = scanner()
                describe({k: v["rssi"] for k, v in nets.items()}, nets, limit=20)
            elif cmd == "p":
                x, y, f, label = parse_xy(rest, "usage: p X Y [FLOOR] [LABEL]")
                if isinstance(scanner, DemoScanner):
                    scanner.pos = (x, y, f)
                print(f"  Measuring at {x}, {y} on floor {f}. Hold still...")
                readings, meta = take_reading(scanner, max(1, args.samples), args.interval)
                if not readings:
                    print("  Nothing heard. Reading not saved.")
                    continue
                for key, n in meta.items():
                    survey["networks"][key] = {"ssid": n.get("ssid"), "channel": n.get("channel"), "band": n.get("band")}
                grow_floor(survey, f, x, y)
                survey["samples"].append({"floor": f, "x": x, "y": y, "label": label,
                                          "readings": readings, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
                save_survey(args.output, survey)
                describe(readings, meta)
                print(f"  Saved reading #{len(survey['samples'])}.")
            elif cmd == "ap":
                x, y, f, name = parse_xy(rest, "usage: ap X Y [FLOOR] [NAME]")
                nets = scanner()
                conn = [(k, v) for k, v in nets.items() if v.get("connected")]
                if not conn:
                    print("  Not connected to a network, so I can't tell which router this is.")
                    continue
                key, n = conn[0]
                grow_floor(survey, f, x, y)
                survey["aps"] = [a for a in survey["aps"] if a.get("bssid") != key]
                survey["aps"].append({"id": f"ap{len(survey['aps']) + 1}", "name": name or n.get("ssid") or "Router",
                                      "bssid": key, "ssid": n.get("ssid"), "floor": f, "x": x, "y": y, "p0": -38})
                survey["networks"][key] = {"ssid": n.get("ssid"), "channel": n.get("channel"), "band": n.get("band")}
                if n.get("band") in ("5", "6"):
                    survey["model"]["band"] = "5"
                save_survey(args.output, survey)
                print(f"  Marked {name or n.get('ssid')} ({key}) at {x}, {y} on floor {f}.")
            elif cmd == "floor":
                if len(rest) < 3:
                    raise ValueError("usage: floor N WIDTH DEPTH [NAME]")
                f = int(rest[0])
                ensure_floor(survey, f)
                survey["floors"][f].update(width=float(rest[1]), depth=float(rest[2]))
                if len(rest) > 3:
                    survey["floors"][f]["name"] = " ".join(rest[3:])
                save_survey(args.output, survey)
                print(f"  Floor {f}: {survey['floors'][f]}")
            elif cmd == "list":
                for i, s in enumerate(survey["samples"], 1):
                    best = max(s["readings"].values()) if s["readings"] else None
                    print(f"  {i:>3}. floor {s['floor']} ({s['x']}, {s['y']}) {s.get('label', ''):<14} strongest {best} dBm")
            elif cmd == "undo":
                if survey["samples"]:
                    survey["samples"].pop()
                    save_survey(args.output, survey)
                    print("  Removed the last reading.")
            else:
                print("  Unknown command. Type 'help'.")
        except ValueError as e:
            print(f"  {e}")

    save_survey(args.output, survey)
    print(f"Saved {len(survey['samples'])} readings to {os.path.abspath(args.output)}")
    print("Open web/index.html and click 'Load survey file' to see it in 3D.")


if __name__ == "__main__":
    main()
