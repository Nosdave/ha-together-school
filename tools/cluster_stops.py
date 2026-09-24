#!/usr/bin/env python3
"""Find a route's real stops from recorded positions.

The backend never exposes a route's stop list, but the bus position does - a
stop can be inferred from where the vehicle repeatedly stands still.

A bus stop and a red light look identical within a single run: the vehicle
stands still for a few seconds either way. They are told apart by RECURRENCE -
a stop happens at the same place on run after run, a traffic light or a jam
does not. Validated by letting it rediscover a stop that was already known from
the API: it landed 36 m away.

Input is one JSON array per line, ``[iso_timestamp, lat, lon]``, e.g. exported
from Home Assistant's history API for the bus tracker. TWO filters matter, and
both were learned the hard way:

* Drop fixes whose ``position_is_current`` attribute is false. The tracker
  deliberately keeps the last position between runs, and the recorder keeps
  storing it - counted naively that becomes a twelve-hour "stop".
* Watch for swapped coordinates in older history. This backend emits
  ``[lat, lng]`` inside a GeoJSON-shaped object, and anything recorded before
  that was understood is the other way round.

    python3 tools/cluster_stops.py positions.jsonl
"""
import json, math, datetime as dt
from collections import defaultdict

DWELL_RADIUS_M = 30      # standing still within this radius counts as a dwell
MIN_DWELL_S    = 25      # shorter than this is just slow traffic
CLUSTER_RADIUS_M = 45    # dwells this close across runs are the same place
RUN_GAP_S      = 20 * 60 # a gap this long separates two runs

def meters(a, b):
    dlat = (a[0] - b[0]) * 111320
    dlon = (a[1] - b[1]) * 111320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)

import sys
SOURCE = sys.argv[1] if len(sys.argv) > 1 else 'positions.jsonl'

pts = []
for line in open(SOURCE):
    ts, lat, lng = json.loads(line)
    pts.append((dt.datetime.fromisoformat(ts.replace('Z', '+00:00')), lat, lng))
pts.sort(key=lambda p: p[0])

# split into runs
runs, cur = [], [pts[0]]
for p in pts[1:]:
    if (p[0] - cur[-1][0]).total_seconds() > RUN_GAP_S:
        runs.append(cur); cur = []
    cur.append(p)
runs.append(cur)
runs = [r for r in runs if len(r) > 5]
print(f"{len(pts)} Punkte -> {len(runs)} Fahrten")
for r in runs:
    print(f"   {r[0][0].astimezone():%d.%m %H:%M} .. {r[-1][0].astimezone():%H:%M}  ({len(r)} Punkte)")

# dwells per run
dwells = []
for idx, run in enumerate(runs):
    i = 0
    while i < len(run):
        j = i
        while j + 1 < len(run) and meters(run[i][1:], run[j + 1][1:]) <= DWELL_RADIUS_M:
            j += 1
        secs = (run[j][0] - run[i][0]).total_seconds()
        if secs >= MIN_DWELL_S:
            lat = sum(p[1] for p in run[i:j + 1]) / (j - i + 1)
            lng = sum(p[2] for p in run[i:j + 1]) / (j - i + 1)
            dwells.append({"run": idx, "lat": lat, "lng": lng, "secs": secs,
                           "t": run[i][0]})
            i = j + 1
        else:
            i += 1
print(f"\nHaltepunkte (>= {MIN_DWELL_S}s stehend): {len(dwells)}")

# cluster across runs
clusters = []
for d in sorted(dwells, key=lambda x: -x["secs"]):
    for c in clusters:
        if meters((d["lat"], d["lng"]), (c["lat"], c["lng"])) <= CLUSTER_RADIUS_M:
            c["members"].append(d); break
    else:
        clusters.append({"lat": d["lat"], "lng": d["lng"], "members": [d]})
for c in clusters:
    c["runs"] = sorted({m["run"] for m in c["members"]})
    c["lat"] = sum(m["lat"] for m in c["members"]) / len(c["members"])
    c["lng"] = sum(m["lng"] for m in c["members"]) / len(c["members"])
    c["median_s"] = sorted(m["secs"] for m in c["members"])[len(c["members"]) // 2]

clusters.sort(key=lambda c: (-len(c["runs"]), -c["median_s"]))
print(f"\n{'Ort':28} {'Fahrten':>8} {'Halte':>6} {'Median s':>9}")
for c in clusters:
    tag = "STOPP" if len(c["runs"]) >= 2 else "einmalig (Ampel/Stau?)"
    print(f"  {c['lat']:.5f},{c['lng']:.5f}  {len(c['runs']):>6}/{len(runs)} "
          f"{len(c['members']):>6} {c['median_s']:>8.0f}  {tag}")
json.dump([{k: v for k, v in c.items() if k != "members"} for c in clusters],
          open('stops.json', 'w'), default=str, indent=1)
print("\ngeschrieben: stops.json")
