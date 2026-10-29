#!/usr/bin/env python3
"""
Worked All States map for a QRZ.com bio page.

Uses the same log sources as make_map.py (QRZ logbook API keys plus any .adi
files in this folder), finds which of the 50 US states you've worked, and
draws docs/was-map.png: confirmed states in one color, worked-but-unconfirmed
in another, with Alaska and Hawaii insets and a list of states still needed.
"""

import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Patch

from make_map import load_qsos, NE

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs" / "was-map.png"
OUT5 = HERE / "docs" / "was-5band.png"
FIVE_BANDS = ["80m", "40m", "20m", "15m", "10m"]
STATES_URL = NE + "ne_50m_admin_1_states_provinces.geojson"

MY_CALL = os.environ.get("MY_CALL", "N0CALL").upper().strip()

STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
          "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
          "WA WV WI WY").split()
US_DXCC = {"291", "6", "110"}          # USA, Alaska, Hawaii
US_NAMES = {"UNITED STATES", "UNITED STATES OF AMERICA", "ALASKA", "HAWAII"}

SEA, LAND, EDGE, TEXT = "#0e2a47", "#2f4f4f", "#86a3a3", "#f2f2f2"
CONFIRMED, WORKED = "#2ca02c", "#e6a100"


def is_us(q):
    dxcc = (q.get("dxcc") or "").strip().lstrip("0")
    if dxcc:
        return dxcc in US_DXCC
    return (q.get("country") or "").strip().upper() in US_NAMES


def is_confirmed(q):
    for f in ("qsl_rcvd", "lotw_qsl_rcvd", "eqsl_qsl_rcvd"):
        if (q.get(f) or "").strip().upper() in ("Y", "V"):
            return True
    return (q.get("app_qrzlog_status") or "").strip().upper() == "C"


def tally(qsos, band=None):
    worked, confirmed = {}, set()
    for q in qsos:
        if not is_us(q):
            continue
        if band and (q.get("band") or "").strip().lower() != band:
            continue
        st = re.sub(r"[^A-Z]", "", (q.get("state") or "").upper())[:2]
        if st not in STATES:
            continue
        worked[st] = worked.get(st, 0) + 1
        if is_confirmed(q):
            confirmed.add(st)
    return worked, confirmed


def load_state_shapes():
    geo = requests.get(STATES_URL, timeout=90).json()
    shapes = {}
    for f in geo["features"]:
        p = f["properties"]
        if p.get("admin") != "United States of America":
            continue
        g = f["geometry"]
        polys = ([g["coordinates"]] if g["type"] == "Polygon"
                 else g["coordinates"] if g["type"] == "MultiPolygon" else [])
        rings = []
        for poly in polys:
            ring = [(x if x < 0 else x - 360, y) for x, y in poly[0]]  # Aleutians
            rings.append(ring)
        shapes[p.get("postal")] = rings
    return shapes


def draw_states(ax, shapes, codes, worked, confirmed):
    polys, colors = [], []
    for st in codes:
        for ring in shapes.get(st, []):
            polys.append(ring)
            colors.append(CONFIRMED if st in confirmed
                          else WORKED if st in worked else LAND)
    ax.add_collection(PolyCollection(polys, facecolors=colors, edgecolors=EDGE,
                                     linewidths=0.5))


def setup(ax, x0, x1, y0, y1):
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect(1 / math.cos(math.radians((y0 + y1) / 2)))
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_facecolor(SEA)
    for s in ax.spines.values():
        s.set_visible(False)


def draw_was(qsos, shapes):
    worked, confirmed = tally(qsos)
    needed = [s for s in STATES if s not in worked]
    print(f"Worked {len(worked)} states, confirmed {len(confirmed)}; "
          f"needed: {' '.join(needed) or 'none'}")

    fig = plt.figure(figsize=(10, 5.4), dpi=100)
    fig.patch.set_facecolor(SEA)

    lower48 = [s for s in STATES if s not in ("AK", "HI")] + ["DC"]
    ax = fig.add_axes([0.03, 0.12, 0.94, 0.70])
    draw_states(ax, shapes, lower48, worked, confirmed)
    setup(ax, -125.5, -66.5, 24, 49.8)

    ak = fig.add_axes([0.02, 0.09, 0.20, 0.24])
    draw_states(ak, shapes, ["AK"], worked, confirmed)
    setup(ak, -190, -129, 51, 72)

    hi = fig.add_axes([0.21, 0.10, 0.12, 0.12])
    draw_states(hi, shapes, ["HI"], worked, confirmed)
    setup(hi, -160.6, -154.6, 18.8, 22.4)

    fig.text(0.5, 0.965, f"{MY_CALL} Worked All States", color=TEXT,
             fontsize=15, fontweight="bold", ha="center", va="top")
    if not needed:
        line = (f"All 50 states worked!  ·  {len(confirmed)} confirmed")
    else:
        line = (f"{len(worked)} of 50 worked  ·  {len(confirmed)} confirmed")
    fig.text(0.5, 0.905, line, color="#c8d3dc", fontsize=10, ha="center",
             va="top")

    fig.legend(handles=[Patch(color=CONFIRMED, label="Confirmed"),
                        Patch(color=WORKED, label="Worked, not confirmed"),
                        Patch(color=LAND, label="Needed")],
               loc="lower right", bbox_to_anchor=(0.985, 0.035), fontsize=8,
               frameon=True, facecolor=SEA, edgecolor=EDGE, labelcolor=TEXT)

    if needed:
        fig.text(0.5, 0.045, "Still needed: " + "  ".join(needed),
                 color="#e5e7eb", fontsize=9, ha="center", va="center")
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(0.99, 0.008, f"Updated {updated}", color="#8fa3b3", fontsize=7,
             ha="right", va="bottom")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor=SEA)
    plt.close(fig)
    print(f"Wrote {OUT}")


def us_panel(fig, rect, shapes, worked, confirmed):
    """One small US map (lower 48 plus tiny AK/HI insets) inside rect."""
    x, y, w, h = rect
    lower48 = [s for s in STATES if s not in ("AK", "HI")] + ["DC"]
    ax = fig.add_axes([x, y + h * 0.05, w, h * 0.95])
    draw_states(ax, shapes, lower48, worked, confirmed)
    setup(ax, -125.5, -66.5, 24, 49.8)
    ak = fig.add_axes([x - w * 0.01, y, w * 0.22, h * 0.32])
    draw_states(ak, shapes, ["AK"], worked, confirmed)
    setup(ak, -190, -129, 51, 72)
    hi = fig.add_axes([x + w * 0.19, y + h * 0.01, w * 0.12, h * 0.15])
    draw_states(hi, shapes, ["HI"], worked, confirmed)
    setup(hi, -160.6, -154.6, 18.8, 22.4)


def draw_5band(qsos, shapes):
    results = {b: tally(qsos, b) for b in FIVE_BANDS}
    fig = plt.figure(figsize=(10, 6.4), dpi=100)
    fig.patch.set_facecolor(SEA)
    fig.text(0.5, 0.975, f"{MY_CALL} 5-Band Worked All States", color=TEXT,
             fontsize=15, fontweight="bold", ha="center", va="top")
    done = [b for b in FIVE_BANDS if len(results[b][0]) == 50]
    total = sum(len(results[b][0]) for b in FIVE_BANDS)
    fig.text(0.5, 0.925, f"{total} of 250 band-states worked  ·  "
             f"{len(done)} of 5 bands complete", color="#c8d3dc", fontsize=10,
             ha="center", va="top")

    cells = [(0.02, 0.47), (0.35, 0.47), (0.68, 0.47), (0.02, 0.05), (0.35, 0.05)]
    pw, ph = 0.30, 0.34
    for band, (cx, cy) in zip(FIVE_BANDS, cells):
        worked, confirmed = results[band]
        us_panel(fig, (cx, cy, pw, ph), shapes, worked, confirmed)
        mark = "  ✓" if len(worked) == 50 else ""
        fig.text(cx + pw / 2, cy + ph + 0.035, f"{band}{mark}", color=TEXT,
                 fontsize=11, fontweight="bold", ha="center", va="bottom")
        fig.text(cx + pw / 2, cy + ph + 0.008,
                 f"{len(worked)}/50 worked · {len(confirmed)} confirmed",
                 color="#c8d3dc", fontsize=8, ha="center", va="bottom")
        print(f"{band}: worked {len(worked)}, confirmed {len(confirmed)}")

    # Sixth cell: what's still needed on each band
    lines = []
    for band in FIVE_BANDS:
        need = [s for s in STATES if s not in results[band][0]]
        if not need:
            lines.append(f"{band}: complete")
        elif len(need) <= 8:
            lines.append(f"{band}: " + " ".join(need))
        else:
            lines.append(f"{band}: {len(need)} states needed")
    fig.text(0.69, 0.39, "Still needed", color=TEXT, fontsize=10,
             fontweight="bold", ha="left", va="top")
    fig.text(0.69, 0.35, "\n".join(lines), color="#e5e7eb", fontsize=8.5,
             ha="left", va="top", linespacing=1.6)
    fig.legend(handles=[Patch(color=CONFIRMED, label="Confirmed"),
                        Patch(color=WORKED, label="Worked, not confirmed"),
                        Patch(color=LAND, label="Needed")],
               loc="lower right", bbox_to_anchor=(0.985, 0.03), fontsize=8,
               frameon=True, facecolor=SEA, edgecolor=EDGE, labelcolor=TEXT)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(0.99, 0.006, f"Updated {updated}", color="#8fa3b3", fontsize=7,
             ha="right", va="bottom")
    OUT5.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT5, facecolor=SEA)
    plt.close(fig)
    print(f"Wrote {OUT5}")


def main():
    qsos = load_qsos()
    shapes = load_state_shapes()
    draw_was(qsos, shapes)
    draw_5band(qsos, shapes)


if __name__ == "__main__":
    main()
