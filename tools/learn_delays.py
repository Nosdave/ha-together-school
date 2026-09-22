#!/usr/bin/env python3
"""Learn how late runs actually are, from the backend's own history.

The scheduled arrival is not what happens: past agendas carry the real
``checkOutTime``, so the typical offset per direction can simply be measured
rather than guessed. Feed the result into the arrival forecast.

Zero dependencies (stdlib only).

    python3 tools/learn_delays.py --school <code> --login '+324XXXXXXXX' \
        --mobile --days 60

The median is used rather than the mean: a single freak run (roadworks, a
breakdown) should not move the everyday expectation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import re
import statistics
import sys
import urllib.error
import urllib.request

BASE = "https://{school}.together-school.com"


def _headers(tenant, locale, token=None):
    h = {"X-TenantID": tenant, "Locale": locale, "Accept": "application/json",
         "Content-Type": "application/json"}
    if token:
        h["Authorization"] = token
    return h


def _call(method, url, headers, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None
    if isinstance(body, dict) and body.get("status") == "SUCCESS" and "data" in body:
        body = body["data"]
    return 200, body


def _minutes(value):
    """Minutes since midnight from a bare "HH:MM:SS+0000" time."""
    if not value:
        return None
    head = value.split("+")[0].split("-")[0]
    parts = [float(x) for x in head.split(":")]
    while len(parts) < 3:
        parts.append(0.0)
    return parts[0] * 60 + parts[1] + parts[2] / 60


def _discover_tenant(base):
    try:
        with urllib.request.urlopen(base + "/environment.values.js", timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"""TENANT_ID\s*:\s*['"]([^'"]+)['"]""", text)
    return m.group(1) if m else None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--school", required=True)
    p.add_argument("--tenant")
    p.add_argument("--locale", default="en")
    p.add_argument("--login", required=True)
    p.add_argument("--mobile", action="store_true", default=True)
    p.add_argument("--device-token", default="ha-learn")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--json-out", help="write the learned offsets here")
    args = p.parse_args()

    base = BASE.format(school=args.school)
    tenant = args.tenant or _discover_tenant(base)
    if not tenant:
        print("Tenant nicht ermittelbar - mit --tenant angeben.")
        return 2
    password = getpass.getpass("Password (not echoed): ")

    st, body = _call("POST", f"{base}/api/v1/auth/signin",
                     _headers(tenant, args.locale),
                     {"login": args.login, "clientType": "MOBILE",
                      "deviceToken": args.device_token, "locale": args.locale,
                      "password": password, "timeZoneOffset": 0})
    if st >= 400 or not isinstance(body, dict) or not body.get("accessToken"):
        print(f"Anmeldung fehlgeschlagen (HTTP {st}).")
        return 1
    hdr = _headers(tenant, args.locale, body["accessToken"])

    _, me = _call("GET", f"{base}/api/v1/auth/users/me", hdr)
    _, pupils = _call("GET", f"{base}/api/v1/parents/{me['userId']}/pupils", hdr)

    report = {}
    for pupil in pupils:
        pid, name = pupil["id"], (pupil.get("forename") or "").title()
        offsets, rows = {}, 0
        for i in range(args.days, -1, -1):
            day = dt.date.today() - dt.timedelta(days=i)
            st, agenda = _call(
                "GET", f"{base}/api/v1/pupils/{pid}/combined/agenda/{day}", hdr)
            if st != 200 or not isinstance(agenda, dict):
                continue
            for r in agenda.get("BUS_ROUTE", []):
                rows += 1
                out, planned = r.get("checkOutTime"), r.get("arrivalTime")
                if not out or not planned:
                    continue
                offsets.setdefault(r.get("direction") or "?", []).append(
                    _minutes(out) - _minutes(planned))

        print(f"\n{name}: {rows} Fahrten in {args.days} Tagen")
        learned = {}
        for direction, values in sorted(offsets.items()):
            values.sort()
            median = statistics.median(values)
            learned[direction] = round(median, 1)
            print(f"  {direction:9} n={len(values):3}  median {median:+.1f} min"
                  f"  mittel {statistics.mean(values):+.1f}"
                  f"  Spanne {values[0]:+.1f} .. {values[-1]:+.1f}")
            if len(values) < 5:
                print("             (wenige Datenpunkte - mit Vorsicht verwenden)")
        report[name or pid] = learned

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"\ngeschrieben: {args.json_out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
