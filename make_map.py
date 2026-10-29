#!/usr/bin/env python3
"""
QSO Map builder for a QRZ.com bio page.

Pulls your QSOs (from the QRZ Logbook API, or from a log.adi file in this
folder), plots every contact on a world map with a line back to your QTH,
and writes docs/qso-map.png. A GitHub Action runs this on a schedule so the
image on your QRZ page stays current.

Settings come from environment variables (set them as GitHub secrets/variables):
  QRZ_API_KEY   QRZ Logbook API key (Logbook > Settings > API). Optional if log.adi exists.
  MY_CALL       Your callsign, e.g. KO4ABC (used for the title and QRZ user agent)
  MY_GRID       Your Maidenhead grid square, e.g. EM85
  MAP_TITLE     Optional custom title
  MAP_VIEW      "auto" (zoom to your contacts, default), "world", or "na" (North America)
"""

import html
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs" / "qso-map.png"
LAND_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
            "master/geojson/ne_50m_land.geojson")
QRZ_URL = "https://logbook.qrz.com/api"

MY_CALL = os.environ.get("MY_CALL", "N0CALL").upper().strip()
MY_GRID = os.environ.get("MY_GRID", "EM85").strip()
API_KEY = os.environ.get("QRZ_API_KEY", "").strip()
VIEW = os.environ.get("MAP_VIEW", "auto").lower().strip()
TITLE = os.environ.get("MAP_TITLE", "").strip() or f"{MY_CALL} QSO Map"

BAND_COLORS = {
    "160m": "#8c564b", "80m": "#d62728", "60m": "#ff7f0e", "40m": "#e6a100",
    "30m": "#bcbd22", "20m": "#2ca02c", "17m": "#17becf", "15m": "#1f77b4",
    "12m": "#6a5acd", "10m": "#9467bd", "6m": "#e377c2", "2m": "#7f7f7f",
    "70cm": "#555555",
}
OTHER_COLOR = "#999999"


# ---------------------------------------------------------------- ADIF ----

def parse_adif(text):
    """Return a list of dicts (lower-case field names) from ADIF text."""
    eoh = re.search(r"<eoh>", text, re.I)
    if eoh:
        text = text[eoh.end():]
    records, rec = [], {}
    pos = 0
    tag = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+)(?::[A-Za-z])?)?>")
    while True:
        m = tag.search(text, pos)
        if not m:
            break
        name = m.group(1).lower()
        if name == "eor":
            if rec:
                records.append(rec)
            rec = {}
            pos = m.end()
            continue
        if m.group(2) is None:
            pos = m.end()
            continue
        length = int(m.group(2))
        rec[name] = text[m.end():m.end() + length]
        pos = m.end() + length
    if rec:
        records.append(rec)
    return records


# ----------------------------------------------------------- QRZ API ----

def qrz_fetch_all(key):
    """Page through the QRZ logbook 250 QSOs at a time."""
    headers = {"User-Agent": f"QSOMapForQRZPage/1.0 ({MY_CALL})"}
    all_recs, after = [], 0
    while True:
        r = requests.post(QRZ_URL, headers=headers, timeout=60, data={
            "KEY": key, "ACTION": "FETCH",
            "OPTION": f"MAX:250,AFTERLOGID:{after}",
        })
        r.raise_for_status()
        body = r.text
        # The ADIF part contains '&' entities, so split it off before parsing.
        head, _, adif = body.partition("ADIF=")
        fields = dict(p.split("=", 1) for p in head.strip("&").split("&") if "=" in p)
        result = fields.get("RESULT", "")
        if result == "FAIL":
            reason = fields.get("REASON", "unknown error")
            if "no log entries" in reason.lower() or "no records" in reason.lower():
                break
            raise RuntimeError(f"QRZ logbook API: {reason}")
        if result == "AUTH":
            raise RuntimeError("QRZ logbook API: key rejected or subscription "
                               "required for FETCH.")
        recs = parse_adif(html.unescape(adif))
        if not recs:
            break
        all_recs.extend(recs)
        ids = [int(x["app_qrzlog_logid"]) for x in recs
               if x.get("app_qrzlog_logid", "").isdigit()]
        if len(recs) < 250 or not ids or max(ids) <= after:
            break
        after = max(ids)
    return all_recs


def api_keys():
    """QRZ_API_KEY plus QRZ_API_KEY_2 ... QRZ_API_KEY_9 (one per callsign logbook)."""
    names = ["QRZ_API_KEY"] + [f"QRZ_API_KEY_{i}" for i in range(2, 10)]
    return [(n, os.environ.get(n, "").strip()) for n in names
            if os.environ.get(n, "").strip()]


def qso_key(q):
    """Identity of a QSO, so one logged in two places is only counted once."""
    return ((q.get("station_callsign") or q.get("operator") or "").upper(),
            (q.get("call") or "").upper(), q.get("qso_date", ""),
            (q.get("time_on") or "")[:4], (q.get("band") or "").lower())


def load_qsos():
    sources = []
    for name, key in api_keys():
        print(f"Fetching QRZ logbook ({name})...")
        recs = qrz_fetch_all(key)
        print(f"  {len(recs)} QSOs")
        sources.append(recs)
    for f in sorted(HERE.glob("*.adi")) + sorted(HERE.glob("*.adif")):
        recs = parse_adif(f.read_text(encoding="utf-8", errors="replace"))
        print(f"Read {f.name}: {len(recs)} QSOs")
        sources.append(recs)
    if not sources:
        sys.exit("No QRZ API key set and no .adi file found - nothing to map.")
    merged, seen = [], set()
    for recs in sources:
        for q in recs:
            k = qso_key(q)
            if k in seen:
                continue
            seen.add(k)
            merged.append(q)
    return merged


# ------------------------------------------------------ geo helpers ----

def grid_to_latlon(grid):
    """Maidenhead (4, 6 or 8 chars) -> center lat, lon. None if invalid."""
    g = (grid or "").strip().upper()
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?", g):
        return None
    lon = (ord(g[0]) - 65) * 20 - 180
    lat = (ord(g[1]) - 65) * 10 - 90
    lon += int(g[2]) * 2
    lat += int(g[3])
    dlon, dlat = 2.0, 1.0
    if len(g) >= 6:
        dlon, dlat = 5 / 60, 2.5 / 60
        lon += (ord(g[4]) - 65) * dlon
        lat += (ord(g[5]) - 65) * dlat
    if len(g) == 8:
        dlon, dlat = dlon / 10, dlat / 10
        lon += int(g[6]) * dlon
        lat += int(g[7]) * dlat
    return lat + dlat / 2, lon + dlon / 2


def adif_coord(v):
    """ADIF LAT/LON like 'N036 01.234' -> decimal degrees."""
    m = re.fullmatch(r"\s*([NSEW])(\d{1,3})\s+(\d{1,2}(?:\.\d+)?)\s*", v or "", re.I)
    if not m:
        return None
    d = int(m.group(2)) + float(m.group(3)) / 60
    return -d if m.group(1).upper() in "SW" else d


def qso_location(q):
    lat, lon = adif_coord(q.get("lat")), adif_coord(q.get("lon"))
    if lat is not None and lon is not None:
        return lat, lon
    return grid_to_latlon(q.get("gridsquare"))


def great_circle(p1, p2, n=64):
    """Points along the great-circle path between two (lat, lon) pairs."""
    la1, lo1, la2, lo2 = map(math.radians, (*p1, *p2))
    d = 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 +
                                math.cos(la1) * math.cos(la2) *
                                math.sin((lo2 - lo1) / 2) ** 2))
    if d == 0:
        return [p1]
    pts = []
    for i in range(n + 1):
        f = i / n
        a = math.sin((1 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)
        x = a * math.cos(la1) * math.cos(lo1) + b * math.cos(la2) * math.cos(lo2)
        y = a * math.cos(la1) * math.sin(lo1) + b * math.cos(la2) * math.sin(lo2)
        z = a * math.sin(la1) + b * math.sin(la2)
        pts.append((math.degrees(math.atan2(z, math.hypot(x, y))),
                    math.degrees(math.atan2(y, x))))
    return pts


def split_dateline(pts):
    """Break a path where it wraps across +/-180 so it doesn't streak the map."""
    segs, cur = [], [pts[0]]
    for a, b in zip(pts, pts[1:]):
        if abs(b[1] - a[1]) > 180:
            segs.append(cur)
            cur = []
        cur.append(b)
    segs.append(cur)
    return [s for s in segs if len(s) > 1]


def load_land():
    try:
        geo = requests.get(LAND_URL, timeout=60).json()
    except Exception as e:  # map still renders without coastlines
        print(f"Could not load coastlines ({e}); drawing without them.")
        return []
    polys = []
    for f in geo.get("features", []):
        g = f["geometry"]
        rings = ([g["coordinates"]] if g["type"] == "Polygon"
                 else g["coordinates"] if g["type"] == "MultiPolygon" else [])
        for poly in rings:
            polys.append([(x, y) for x, y in poly[0]])
    return polys


# ------------------------------------------------------------- drawing ----

def main():
    home = grid_to_latlon(MY_GRID)
    if not home:
        sys.exit(f"MY_GRID '{MY_GRID}' is not a valid grid square.")

    qsos = load_qsos()
    plotted, skipped, seen = [], 0, set()
    bands = {}
    qths = {home}
    for q in qsos:
        loc = qso_location(q)
        if not loc:
            skipped += 1
            continue
        # Draw the path from where this QSO was made, if the log says.
        origin = grid_to_latlon(q.get("my_gridsquare")) or home
        qths.add(origin)
        band = (q.get("band") or "").lower()
        key = (q.get("call", "").upper(), round(loc[0], 2), round(loc[1], 2),
               band, round(origin[0], 1), round(origin[1], 1))
        if key in seen:
            continue
        seen.add(key)
        plotted.append((loc, band, origin))
        bands[band] = bands.get(band, 0) + 1
    calls = {q.get("call", "").upper() for q in qsos if q.get("call")}
    # Countries = DXCC entities (falls back to the country name if no number)
    countries = set()
    for q in qsos:
        dxcc = (q.get("dxcc") or "").strip().lstrip("0")
        if dxcc and dxcc.isdigit():
            countries.add(("dxcc", dxcc))
        elif (q.get("country") or "").strip():
            countries.add(("name", q["country"].strip().upper()))
    # A country seen by name only is dropped if the same country has a number elsewhere
    names_with_num = {(q.get("country") or "").strip().upper() for q in qsos
                      if (q.get("dxcc") or "").strip().lstrip("0").isdigit()}
    countries = {c for c in countries if not (c[0] == "name" and c[1] in names_with_num)}
    my_calls = sorted({(q.get("station_callsign") or q.get("operator") or "").upper()
                       for q in qsos} - {""})
    if MY_CALL in my_calls:  # main call first
        my_calls.remove(MY_CALL)
        my_calls.insert(0, MY_CALL)
    print(f"{len(qsos)} QSOs, {len(calls)} unique calls, {len(countries)} countries, "
          f"{len(plotted)} plotted, {skipped} without location.")

    # Map extent
    if VIEW == "world" or (VIEW == "auto" and not plotted):
        x0, x1, y0, y1 = -180, 180, -60, 85
    elif VIEW == "na":
        x0, x1, y0, y1 = -170, -50, 10, 75
    else:
        lats = [p[0][0] for p in plotted] + [q[0] for q in qths]
        lons = [p[0][1] for p in plotted] + [q[1] for q in qths]
        x0, x1 = min(lons) - 8, max(lons) + 8
        y0, y1 = min(lats) - 6, max(lats) + 6
        # keep a pleasant 2:1 shape
        w, h = x1 - x0, y1 - y0
        if w < 2 * h:
            pad = (2 * h - w) / 2
            x0, x1 = x0 - pad, x1 + pad
        else:
            pad = (w / 2 - h) / 2
            y0, y1 = y0 - pad, y1 + pad
        x0, x1 = max(x0, -180), min(x1, 180)
        y0, y1 = max(y0, -85), min(y1, 85)

    sea, land_c, edge, text = "#0e2a47", "#2f4f4f", "#4a6b6b", "#f2f2f2"
    fig, ax = plt.subplots(figsize=(10, 5.4), dpi=100)
    fig.patch.set_facecolor(sea)
    ax.set_facecolor(sea)

    land = load_land()
    if land:
        ax.add_collection(PolyCollection(land, facecolor=land_c,
                                         edgecolor=edge, linewidth=0.4, zorder=1))
    for lon in range(-180, 181, 30):
        ax.axvline(lon, color="#ffffff", alpha=0.06, lw=0.5, zorder=0)
    for lat in range(-90, 91, 30):
        ax.axhline(lat, color="#ffffff", alpha=0.06, lw=0.5, zorder=0)

    # Paths and points
    for (loc, band, origin) in plotted:
        color = BAND_COLORS.get(band, OTHER_COLOR)
        for seg in split_dateline(great_circle(origin, loc)):
            ax.add_collection(LineCollection([[(lo, la) for la, lo in seg]],
                                             colors=color, linewidths=0.6,
                                             alpha=0.35, zorder=2))
    for (loc, band, _) in plotted:
        ax.scatter(loc[1], loc[0], s=14, color=BAND_COLORS.get(band, OTHER_COLOR),
                   edgecolors="white", linewidths=0.3, zorder=3)
    for qth in qths:
        ax.scatter(qth[1], qth[0], s=90, marker="*", color="#ffd700",
                   edgecolors="black", linewidths=0.6, zorder=4)

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    # Legend (bands actually worked, in band order)
    order = list(BAND_COLORS)
    used = sorted(bands, key=lambda b: order.index(b) if b in order else 99)
    handles = [plt.Line2D([], [], marker="o", ls="", color=BAND_COLORS.get(b, OTHER_COLOR),
                          label=f"{b or 'other'} ({bands[b]})") for b in used]
    if handles:
        leg = ax.legend(handles=handles, loc="lower left", fontsize=8, ncol=1,
                        frameon=True, facecolor=sea, edgecolor=edge, labelcolor=text)
        leg.get_frame().set_alpha(0.85)

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(0.5, 0.965, TITLE, color=text, fontsize=15, fontweight="bold",
             ha="center", va="top")
    worked_as = "" if len(my_calls) > 1 else f"  ·  from {MY_GRID.upper()}"
    fig.text(0.5, 0.905, f"{len(calls)} stations worked  ·  {len(qsos)} QSOs  ·  "
             f"{len(countries)} countries{worked_as}", color="#c8d3dc", fontsize=9,
             ha="center", va="top")
    fig.text(0.99, 0.01, f"Updated {updated}", color="#8fa3b3",
             fontsize=7, ha="right", va="bottom")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.04, top=0.87)
    fig.savefig(OUT, facecolor=sea)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
