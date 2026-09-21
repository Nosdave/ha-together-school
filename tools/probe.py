#!/usr/bin/env python3
"""Standalone auth + data probe for the Together School backend.

Zero dependencies (stdlib urllib only). No phone, proxy or root needed.

Parent accounts sign in with the MOBILE client type, using the **mobile phone
number** registered in the child's profile (not the email address):

    python3 tools/probe.py --school <school_code> --login '+324XXXXXXXX' --mobile

The password is prompted for, never taken on the command line. Reuse the printed
deviceToken on later runs so you stay one registered device:

    python3 tools/probe.py --school <school_code> --login '+324XXXXXXXX' \
        --mobile --device-token 'ha-...' --days 2

Timetable vs. live position: the agenda endpoint is populated as soon as a
school day is scheduled, so it can be inspected at any hour. The bus location
only fills in while a run is actually happening.

If you have no password yet, set one via the emailed code:

    python3 tools/probe.py --school <school_code> --login '+324XXXXXXXX' \
        --request-reset
    python3 tools/probe.py --school <school_code> --login '+324XXXXXXXX' \
        --code 123456 --set-password 'NewStrongPass!'

Nothing is written locally; only the official API is contacted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import re
import sys
import uuid
import urllib.error
import urllib.request

BASE = "https://{school}.together-school.com"
ENVIRONMENT_PATH = "/environment.values.js"


def _discover_tenant(base):
    """Read TENANT_ID from the deployment's public web config."""
    try:
        req = urllib.request.Request(f"{base}{ENVIRONMENT_PATH}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    match = re.search(r"""TENANT_ID\s*:\s*['\"]([^'\"]+)['\"]""", text)
    return match.group(1) if match else None


def _headers(tenant, locale, token=None):
    h = {
        "X-TenantID": tenant,
        "Locale": locale,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        h["Authorization"] = token  # raw, no "Bearer "
    return h


def _call(method, url, headers, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _unwrap(_parse(resp.read().decode("utf-8", "replace")))
    except urllib.error.HTTPError as e:
        return e.code, _unwrap(_parse(e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        return 0, {"_error": str(e)}


def _unwrap(body):
    """Responses are wrapped as {status:SUCCESS, data:<payload>}. Return the
    inner payload on success; leave error envelopes intact so we can see them."""
    if isinstance(body, dict) and body.get("status") == "SUCCESS" \
            and "data" in body:
        return body["data"]
    return body


def _parse(body):
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return body


def _full(obj):
    """Print a payload in full - used where we are hunting for field names."""
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except TypeError:
        return str(obj)


def _short(obj, limit=900):
    try:
        s = json.dumps(obj, indent=2, ensure_ascii=False)
    except TypeError:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "\n… (truncated)"


def _pick_id(obj):
    if isinstance(obj, dict):
        for key in ("userId", "id", "parentId", "uuid"):
            if obj.get(key):
                return str(obj[key])
    return None


def _extract_pupil_ids(pupils):
    if isinstance(pupils, dict):
        pupils = pupils.get("items") or pupils.get("pupils") or []
    ids = []
    if isinstance(pupils, list):
        for it in pupils:
            pid = _pick_id(it) if isinstance(it, dict) else None
            if pid:
                ids.append(pid)
    return ids


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--school", required=True,
                   help="subdomain of the school's deployment")
    p.add_argument("--tenant",
                   help="tenant id; discovered automatically when omitted")
    p.add_argument("--locale", default="en")
    p.add_argument("--token")
    p.add_argument("--login")
    p.add_argument("--password")
    p.add_argument("--request-reset", action="store_true",
                   help="POST /auth/password/forgot to email a code")
    p.add_argument("--code", help="the code you received by email")
    p.add_argument("--set-password",
                   help="new password to set together with --code")
    p.add_argument("--mobile", action="store_true",
                   help="sign in as the mobile app (clientType MOBILE + a "
                        "device token). Parent accounts require this.")
    p.add_argument("--device-token",
                   help="device token to send in MOBILE mode; if omitted a "
                        "random stable-looking one is generated and printed. "
                        "Reuse the SAME value in HA so it stays one 'device'.")
    p.add_argument("--date",
                   help="first agenda date to fetch (YYYY-MM-DD, default today)")
    p.add_argument("--days", type=int, default=1,
                   help="how many consecutive days of agenda to fetch")
    p.add_argument("--confirm-code",
                   help="if MOBILE sign-in asks for a device confirmation code "
                        "(sent by SMS/email), pass it here to confirm")
    args = p.parse_args()

    base = BASE.format(school=args.school)
    if not args.tenant:
        args.tenant = _discover_tenant(base)
        if not args.tenant:
            print(f"Could not read TENANT_ID from {base}{ENVIRONMENT_PATH} - "
                  "pass it with --tenant.")
            return 2
        print(f">>> Discovered tenant: {args.tenant}")
    hdr_noauth = _headers(args.tenant, args.locale)
    token = args.token

    # Never force the password onto the command line (shell history / ps).
    if args.login and not args.password and not args.request_reset \
            and not args.code and not token:
        args.password = getpass.getpass("Password (not echoed): ")

    # A) request a reset code ---------------------------------------------
    if args.request_reset:
        if not args.login:
            print("--request-reset needs --login")
            return 2
        print("== POST /api/v1/auth/password/forgot ==")
        st, body = _call("POST", f"{base}/api/v1/auth/password/forgot",
                         hdr_noauth, {"login": args.login})
        print(f"HTTP {st}")
        print(_short(body))
        print("\n>>> Check your email for the code, then run with "
              "--code <code> --set-password '<newpass>'.")
        return 0 if st < 400 else 1

    # B) confirm the code + set a new password ----------------------------
    if args.code and args.set_password:
        if not args.login:
            print("--code/--set-password need --login")
            return 2
        print("== POST /api/v1/auth/password/confirm ==")
        st, body = _call("POST", f"{base}/api/v1/auth/password/confirm",
                         hdr_noauth,
                         {"login": args.login, "code": args.code,
                          "password": args.set_password})
        print(f"HTTP {st}")
        print(_short(body))
        if st >= 400:
            print("\n>>> Setting the password failed - see body above.")
            return 1
        print("\n>>> Password set. Continuing with sign-in...\n")
        args.password = args.set_password

    # C) credential sign-in -----------------------------------------------
    if args.login and args.password and not token:
        if args.mobile:
            device_token = args.device_token or ("ha-" + uuid.uuid4().hex)
            print(f">>> Using clientType MOBILE, deviceToken={device_token}")
            print(">>> REUSE this exact deviceToken in HA so it stays one "
                  "device.\n")
            payload = {"login": args.login, "clientType": "MOBILE",
                       "deviceToken": device_token, "locale": args.locale,
                       "password": args.password, "timeZoneOffset": 0}
        else:
            payload = {"login": args.login, "clientType": "WEB",
                       "locale": args.locale, "password": args.password,
                       "timeZoneOffset": 0}
        print("== POST /api/v1/auth/signin ==")
        st, body = _call("POST", f"{base}/api/v1/auth/signin", hdr_noauth,
                         payload)
        print(f"HTTP {st}")
        print(_short(body))
        if st < 400 and isinstance(body, dict) and body.get("accessToken"):
            token = body["accessToken"]
            print("\n>>> RESULT: sign-in returned a token.\n")
            # If a confirmation code was supplied, run the confirm step.
            if args.confirm_code:
                print("== POST /api/v1/auth/signin/confirm ==")
                cst, cbody = _call(
                    "POST", f"{base}/api/v1/auth/signin/confirm", hdr_noauth,
                    {"accessToken": token,
                     "temporaryPassword": args.confirm_code})
                print(f"HTTP {cst}")
                print(_short(cbody))
                if isinstance(cbody, dict) and cbody.get("accessToken"):
                    token = cbody["accessToken"]
        else:
            print("\n>>> RESULT: no usable token. 403 'use the mobile app' "
                  "means parent accounts must use --mobile. 401 means wrong "
                  "identifier/password (parents: the MOBILE PHONE NUMBER, "
                  "exactly as in the browser). If a device code was sent to "
                  "you, re-run adding --confirm-code <code>.\n")

    if not token:
        print("No usable token - stopping.")
        return 1

    hdr = _headers(args.tenant, args.locale, token)
    print(f"\n>>> ACCESS TOKEN (store this if using token mode):\n{token}\n")

    # D) walk the parent data ---------------------------------------------
    print("== GET /api/v1/auth/users/me ==")
    st, me = _call("GET", f"{base}/api/v1/auth/users/me", hdr)
    print(f"HTTP {st}")
    print(_short(me))
    parent_id = _pick_id(me)
    print(f"\nparent/user id -> {parent_id}\n")
    if not parent_id:
        return 1

    print(f"== GET /api/v1/parents/{parent_id}/pupils ==")
    st, pupils = _call("GET", f"{base}/api/v1/parents/{parent_id}/pupils", hdr)
    print(f"HTTP {st}")
    print(_short(pupils))
    pupil_ids = _extract_pupil_ids(pupils)
    print(f"\npupil ids -> {pupil_ids}\n")

    # Payloads below are printed in FULL (no truncation): the whole point is to
    # discover field names, and a timetable easily exceeds any summary limit.
    start = (
        dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    )
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(args.days)]

    for pid in pupil_ids[:2]:
        print(f"== GET deliveryWithBus/location (pupil {pid}) ==")
        st, d = _call(
            "GET",
            f"{base}/api/v1/transportation/pupils/{pid}/deliveryWithBus/location",
            hdr)
        print(f"HTTP {st}")
        print(_full(d))

        for day in dates:
            print(f"\n== GET combined/agenda/{day} (pupil {pid}) ==")
            st, a = _call(
                "GET", f"{base}/api/v1/pupils/{pid}/combined/agenda/{day}", hdr)
            print(f"HTTP {st}")
            print(_full(a))
        print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
