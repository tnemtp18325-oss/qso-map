#!/usr/bin/env python3
"""
Spots map for a QRZ.com bio page: where your signal has been heard lately.

Every run (a GitHub Action runs it every 15 minutes) it collects new spots of
your callsign from:
  - PSK Reporter          (FT8, FT4, PSK and other digital modes)
  - Reverse Beacon Network (CW / RTTY skimmers, via HamQTH)
  - DX cluster            (spots posted by other hams, e.g. SSB, via HamQTH)
keeps them in data/spots.json, and draws docs/spots-map.png showing everyone
who heard you in the last 24 hours plus an ON THE AIR / last-heard banner.

Settings (GitHub variables):
  MY_CALL      callsign to watch, e.g. KZ5MH
  MY_GRID      your grid square
  SPOT_CALLS   optional, extra callsigns to watch, comma separated (e.g. KZ5MH/P)
"""

import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection

from make_map import (BAND_COLORS, OTHER_COLOR, grid_to_latlon, great_circle,
                      split_dateline, load_land)

HERE = Path(__file__).resolve().parent
STATE = HERE / "data" / "spots.json"
OUT = HERE / "docs" / "spots-map.png"

MY_CALL = os.environ.get("MY_CALL", "N0CALL").upper().strip()
MY_GRID = os.environ.get("MY_GRID", "EM85").strip()
CALLS = [MY_CALL] + [c.strip().upper() for c in
                     os.environ.get("SPOT_CALLS", "").split(",") if c.strip()]
UA = {"User-Agent": f"QSOMapForQRZPage-spots/1.0 ({MY_CALL})"}

WINDOW = 24 * 3600          # spots drawn on the map
ON_AIR = 30 * 60            # "ON THE AIR" if heard within this many seconds
KEEP = 30 * 24 * 3600       # history kept for "last heard"

BANDS = [(1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.3, 5.45, "60m"),
         (7.0, 7.3, "40m"), (10.1, 10.15, "30m"), (14.0, 14.35, "20m"),
         (18.068, 18.168, "17m"), (21.0, 21.45, "15m"), (24.89, 24.99, "12m"),
         (28.0, 29.7, "10m"), (50.0, 54.0, "6m"), (144.0, 148.0, "2m"),
         (420.0, 450.0, "70cm")]


def band_of(mhz):
    for lo, hi, name in BANDS:
        if lo <= mhz <= hi:
            return name
    return ""


def latlon_of(grid):
    g = re.sub(r"[^A-Za-z0-9]", "", grid or "")[:6]
    if len(g) not in (4, 6):
        g = g[:4]
    return grid_to_latlon(g)


# ------------------------------------------------------------- sources ----

def fetch_pskreporter():
    """Reception reports from the last 2 hours (runs are every 15 minutes)."""
    out = []
    for call in CALLS:
        params = {"senderCallsign": call, "rronly": 1, "noactive": 1,
                  "flowStartSeconds": -7200}
        r = requests.get("https://retrieve.pskreporter.info/query",
                         params=params, headers=UA, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for rr in root.iter("receptionReport"):
            loc = latlon_of(rr.get("receiverLocator"))
            if not loc:
                continue
            mhz = int(rr.get("frequency") or 0) / 1e6
            out.append({"src": "psk", "spotter": rr.get("receiverCallsign"),
                        "lat": loc[0], "lon": loc[1], "band": band_of(mhz),
                        "mode": rr.get("mode") or "", "freq": round(mhz, 4),
                        "snr": rr.get("sNR"),
                        "t": int(rr.get("flowStartSeconds"))})
    return out


def hamqth(path, **params):
    params.update({"calls": ",".join(CALLS), "include": "locations",
                   "format": "json"})
    r = requests.get(f"https://www.hamqth.com/api/v2/{path}", params=params,
                     headers=UA, timeout=60)
    r.raise_for_status()
    data = r.json()
    locs = {l["call"].upper(): (l["lat"], l["lon"])
            for l in data.get("locations", []) if l.get("lat") is not None}
    return data.get("spots", []), locs


def fetch_rbn(now):
    spots, locs = hamqth("rbn", maxAge=3600)
    out = []
    for s in spots:
        if s.get("dxcall", "").upper().split("/")[0] not in \
                {c.split("/")[0] for c in CALLS}:
            continue
        for sk in s.get("skimmers", []):
            loc = locs.get(sk["call"].upper())
            if not loc:
                continue
            out.append({"src": "rbn", "spotter": sk["call"], "lat": loc[0],
                        "lon": loc[1], "band": s.get("band", "").lower(),
                        "mode": s.get("mode") or "CW",
                        "freq": round(float(s.get("freq", 0)) / 1000, 4),
                        "snr": sk.get("snr"), "t": now - int(s.get("age", 0))})
    return out


def fetch_cluster():
    spots, locs = hamqth("spots", limit=200)
    out = []
    for s in spots:
        if s.get("dxcall", "").upper().split("/")[0] not in \
                {c.split("/")[0] for c in CALLS}:
            continue
        loc = locs.get(s.get("spotter", "").upper())
        if not loc:
            continue
        t = int(datetime.fromisoformat(s["time"].replace("Z", "+00:00")).timestamp())
        out.append({"src": "cluster", "spotter": s["spotter"], "lat": loc[0],
                    "lon": loc[1], "band": s.get("band", "").lower(),
                    "mode": s.get("mode") or "", "freq": round(float(s.get("freq", 0)) / 1000, 4),
                    "snr": None, "comment": s.get("comment", ""), "t": t})
    return out


# ----------------------------------------------------------------- state ----

def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"spots": {}}


def merge(state, new):
    """One entry per (source, spotter, band, mode), keeping the latest time."""
    db = state.setdefault("spots", {})
    for s in new:
        if not s.get("spotter"):
            continue
        k = f'{s["src"]}|{s["spotter"].upper()}|{s["band"]}|{s["mode"]}'
        if k not in db or s["t"] >= db[k]["t"]:
            db[k] = s
    cutoff = time.time() - KEEP
    state["spots"] = {k: v for k, v in db.items() if v["t"] >= cutoff}


# -------------------------------------------------------------- drawing ----

def miles(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def utc(t, fmt="%H:%M UTC"):
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


def draw(state, now):
    home = grid_to_latlon(MY_GRID)
    allspots = list(state["spots"].values())
    recent = [s for s in allspots if s["t"] >= now - WINDOW]
    last = max(allspots, key=lambda s: s["t"]) if allspots else None

    sea, land_c, edge, text = "#0e2a47", "#2f4f4f", "#4a6b6b", "#f2f2f2"
    fig, ax = plt.subplots(figsize=(10, 5.4), dpi=100)
    fig.patch.set_facecolor(sea)
    ax.set_facecolor(sea)

    if recent:
        lats = [s["lat"] for s in recent] + [home[0]]
        lons = [s["lon"] for s in recent] + [home[1]]
        x0, x1 = min(lons) - 10, max(lons) + 10
        y0, y1 = min(lats) - 7, max(lats) + 7
        w, h = x1 - x0, y1 - y0
        if w < 2 * h:
            x0, x1 = x0 - (2 * h - w) / 2, x1 + (2 * h - w) / 2
        else:
            y0, y1 = y0 - (w / 2 - h) / 2, y1 + (w / 2 - h) / 2
        if x1 - x0 < 60:  # don't zoom in tighter than a region
            c = (x0 + x1) / 2; x0, x1 = c - 30, c + 30
            c = (y0 + y1) / 2; y0, y1 = c - 15, c + 15
        x0, x1 = max(x0, -180), min(x1, 180)
        y0, y1 = max(y0, -85), min(y1, 85)
    else:
        x0, x1, y0, y1 = -180, 180, -60, 85

    land = load_land()
    if land:
        ax.add_collection(PolyCollection(land, facecolor=land_c, edgecolor=edge,
                                         linewidth=0.4, zorder=1))
    for lon in range(-180, 181, 30):
        ax.axvline(lon, color="#ffffff", alpha=0.06, lw=0.5, zorder=0)
    for lat in range(-90, 91, 30):
        ax.axhline(lat, color="#ffffff", alpha=0.06, lw=0.5, zorder=0)

    bands = {}
    for s in recent:
        c = BAND_COLORS.get(s["band"], OTHER_COLOR)
        bands[s["band"]] = bands.get(s["band"], 0) + 1
        for seg in split_dateline(great_circle(home, (s["lat"], s["lon"]))):
            ax.add_collection(LineCollection([[(lo, la) for la, lo in seg]],
                                             colors=c, linewidths=0.7,
                                             alpha=0.45, zorder=2))
    for s in recent:
        ax.scatter(s["lon"], s["lat"], s=16,
                   color=BAND_COLORS.get(s["band"], OTHER_COLOR),
                   edgecolors="white", linewidths=0.3, zorder=3)
    ax.scatter(home[1], home[0], s=90, marker="*", color="#ffd700",
               edgecolors="black", linewidths=0.6, zorder=4)

    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    order = list(BAND_COLORS)
    used = sorted(bands, key=lambda b: order.index(b) if b in order else 99)
    handles = [plt.Line2D([], [], marker="o", ls="",
                          color=BAND_COLORS.get(b, OTHER_COLOR),
                          label=f"{b or 'other'} ({bands[b]})") for b in used]
    if handles:
        leg = ax.legend(handles=handles, loc="lower left", fontsize=8,
                        frameon=True, facecolor=sea, edgecolor=edge,
                        labelcolor=text)
        leg.get_frame().set_alpha(0.85)

    # Title, status banner, summary
    fig.text(0.5, 0.965, f"{MY_CALL} Live Spots", color=text, fontsize=15,
             fontweight="bold", ha="center", va="top")
    if last and now - last["t"] <= ON_AIR:
        status = (f"●  ON THE AIR  ·  last spotted {utc(last['t'])} on "
                  f"{last['band']} {last['mode']}")
        color, bg = "#0b2e13", "#4ade80"
    elif last:
        status = (f"Last spotted {utc(last['t'], '%b %d, %H:%M UTC')} on "
                  f"{last['band']} {last['mode']}")
        color, bg = "#e5e7eb", "#374151"
    else:
        status = "No recent spots"
        color, bg = "#e5e7eb", "#374151"
    fig.text(0.5, 0.895, status, color=color, fontsize=10, fontweight="bold",
             ha="center", va="top",
             bbox=dict(boxstyle="round,pad=0.4", facecolor=bg, edgecolor="none"))

    if recent:
        spotters = {s["spotter"].upper() for s in recent}
        far = max(recent, key=lambda s: miles(home, (s["lat"], s["lon"])))
        summary = (f"Heard by {len(spotters)} stations in the last 24 hours  ·  "
                   f"farthest {miles(home, (far['lat'], far['lon'])):,.0f} mi "
                   f"({far['spotter']})")
    else:
        summary = "Not heard in the last 24 hours"
    fig.text(0.5, 0.835, summary, color="#c8d3dc", fontsize=9, ha="center",
             va="top")
    fig.text(0.01, 0.01, "Spots: PSK Reporter · Reverse Beacon Network · "
             "DX cluster (via HamQTH)", color="#8fa3b3", fontsize=7,
             ha="left", va="bottom")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.04, top=0.80)
    fig.savefig(OUT, facecolor=sea)
    print(f"{len(recent)} spots in window; wrote {OUT}")


def main():
    now = int(time.time())
    state = load_state()
    new = []
    for name, fn in (("PSK Reporter", fetch_pskreporter),
                     ("RBN", lambda: fetch_rbn(now)),
                     ("DX cluster", fetch_cluster)):
        try:
            got = fn()
            print(f"{name}: {len(got)} spots")
            new += got
        except Exception as e:  # one source down shouldn't stop the map
            print(f"{name}: failed ({e})")
    merge(state, new)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0, sort_keys=True))
    draw(state, now)


if __name__ == "__main__":
    main()
