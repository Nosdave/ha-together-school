# Developer notes – reverse-engineered API map

Source: the vendor's official Android app (package `com.together_school.<school>`,
v1.4.5 / versionCode 223), built on `com.instinctools.educe`. Static analysis
only (baksmali of the DEX). Endpoints below are vendor-wide, not school-specific.

## Backend

- Base URL: `https://<school_code>.together-school.com`, one tenant per school
- Each app flavour hard-codes its own `API_BASE_URL`, `TENANT_ID` and
  `FLAVOR_school`; the tenant is **not** the same across schools, so discover it
  from `GET /environment.values.js` (`TENANT_ID: "..."`) rather than guessing
- Stack: Retrofit + RxJava + Gson (AutoValue), Room DB. No cert pinning in app code.

## Mandatory headers (interceptors)

- `ApiAccessTokenInterceptor` → `Authorization: %s` (raw token, **no** Bearer)
- `ApiTenantIdInterceptor`    → `X-TenantID: <tenant>` (per school)
- `ApiLocaleInterceptor`      → `Locale: <locale>`

## Auth (AuthApiService)

- `POST /api/v1/auth/users/verify`      {login}            → sends code
- `POST /api/v1/auth/signin`            {login,password,clientType:"MOBILE",
                                          deviceToken,locale,timeZoneOffset}
                                        → SignInResponse{accessToken,roles,termsAccepted}
- `POST /api/v1/auth/signin/confirm`    {accessToken,temporaryPassword}  (device OTP)
- `POST /api/v1/auth/signin/role/confirm`
- `PUT  /api/v1/auth/password`          {…}  (changePassword)
- `POST /api/v1/auth/password/forgot`   (resetPassword)
- `POST /api/v1/auth/password/confirm`
- `GET  /api/v1/auth/users/me`          → current user (parent id)
- `POST /api/v1/auth/signout`

Password reset / set flow (fully headless-capable, no phone/proxy/root):
- `POST /api/v1/auth/password/forgot`  {login}                 → emails a code
- `POST /api/v1/auth/password/confirm` {login, code, password} → sets password
- then `POST /api/v1/auth/signin`      {login, password}       → accessToken
- `PUT  /api/v1/auth/password`         {oldPassword, newPassword}

Login UI keys: verify_phone → verify_code → set_new_password_sign_in. The email
link + verification key is one-time onboarding, not a per-login step. NOTE:
`auth/verify`, `auth/users/verify` and `auth/signin/confirm` exist in
AuthApiService but are NOT wired into the current app flow (v1.4.5); the live
flow is verifyUser (branch) → signIn. Target SDK 34, NO network_security_config
present → the app does not trust user-added CA certs, so mitmproxy on an
un-rooted phone will not decrypt traffic. Prefer the API password flow above.

Token persisted in Room DB `parent-data`, table `app_session`
(`CREATE TABLE app_session (sessionId, userId, access_token, avaliable_roles,
role, is_crisis_team_member, is_notices_sync_required, agreementId, text,
version)`). Path on device: `/data/data/com.together_school.<school>/databases/parent-data`.

## Parent / pupil data

- `GET /api/v1/parents/{parentId}/pupils`
- `GET /api/v1/pupils/{pupilId}`
- `GET /api/v1/pupils/{pupilId}/combined/agenda/{date}`   (check-in/out)
- `GET /api/v1/pupils/{pupilId}/agenda/{date}/routes/{activeRouteId}`
- `GET /api/v1/pupils/{pupilId}/insurances`

## Transportation (TransportationApiService)

- `GET /api/v1/transportation/pupils/{pupilId}/deliveryWithBus/location`
  → PupilDeliveryWithBusResponse{busLocation{busId,location}, studentLocation,
    schoolLocation, stationLocation}  ← primary parent endpoint
- `GET /api/v1/transportation/buses/{busId}/location`
- `GET /api/v1/transportation/drivers/{driverId}/routes/active/delays`
- driver/supervisor endpoints exist but are staff-only.

## Absences

- `POST /api/v1/students/{studentId}/absences`
- `GET  /api/v1/students/{studentId}/absences/search/actual`
- `POST /api/v1/students/{studentId}/absences/{absenceId}/stop`
- `GET  /api/v1/students/absences/reasons`

## CONFIRMED against a live backend with a real parent account (2026-09-20)

- **Response envelope:** every response is `{"status":"SUCCESS","data":<payload>}`
  (errors: `{"status":"ERROR","message":...,"errors":...}`). Unwrap before use.
- **Parent accounts cannot use clientType WEB** -> HTTP 403 "You are not
  authorized to log in to the web application. Please use our mobile app
  instead." Must use `clientType: MOBILE`.
- **MOBILE requires a non-empty `deviceToken`**, otherwise HTTP 400
  "Edit device token according with client type." Any opaque string is accepted;
  we generate `ha-<uuid4hex>` once per config entry and reuse it.
- **The login identifier is the MOBILE PHONE NUMBER** (E.164, e.g. `+324...`),
  not the email address. Wrong identifier/password -> HTTP 401.
- Working sign-in body:
  `{login, clientType:"MOBILE", deviceToken, locale, password, timeZoneOffset}`
- `GET /auth/users/me` -> `{userId, fullName, email, phoneNumber, roles:["parent"], ...}`
- `GET /parents/{userId}/pupils` -> **plain JSON array**; pupil has
  `{id, forename, surname, fullName, active, address[], accompanied, locationOn, ...}`
  (names come back in CAPS).
- `GET /transportation/pupils/{id}/deliveryWithBus/location` -> keys confirmed:
  `{studentLocation, busLocation, schoolLocation, stationLocation}`; all `null`
  outside service hours.
- `GET /pupils/{id}/combined/agenda/{date}` -> `{"BUS_ROUTE": []}` when idle.

## BUS_ROUTE schema (confirmed on a school day, 2026-09-21)

`GET /pupils/{id}/combined/agenda/{date}` -> `{"BUS_ROUTE": [ {...} ]}`, one
entry per run. **The agenda is populated as soon as a school day is scheduled**,
independent of whether a bus is currently driving - so it can be inspected at
any hour, unlike the live location.

```json
{"busId": null, "activeRouteId": null, "studentState": null, "routeState": null,
 "direction": "WAY_TO", "schedule": "05:40:00+0000",
 "arrivalTime": "06:05:00+0000", "checkInTime": null, "checkOutTime": null,
 "missedTime": null, "inOtherStop": false, "busNumber": "99XX1",
 "boardingStation": "<stop>", "arrivalStation": "<school>",
 "startTime": "2026-09-21T05:40:00+0000", "online": false,
 "next": "AUTO", "name": "99XX1"}
```

Two things that are easy to get wrong:

- **Check-in/out are TIMESTAMPS, not booleans** (`checkInTime`, `checkOutTime`,
  `missedTime`; `null` until they happen). There is no boolean "on bus" field -
  it is derived: checked in and not yet checked out.
- **All times are UTC** (`+0000`). A `05:40` departure is a 07:40 Brussels
  pickup. `schedule`/`arrivalTime` are time-of-day only and must be anchored to
  the date of `startTime`; `startTime` itself is a full ISO datetime.

`busId`, `activeRouteId`, `studentState` and `routeState` stay `null` until a
run is actually live; `online` flags a trackable bus. `direction` is `WAY_TO`
(to school) and presumably `WAY_BACK` for the return leg.

Derived status used by the integration: `no_service` -> `scheduled` ->
`on_route` (online) -> `on_board` (checkInTime) -> `completed` (checkOutTime),
with `missed` (missedTime) taking precedence.

Note: the pupil record's `"active": false` does **not** mean transport is
inactive - runs are scheduled regardless.

## Live run payload (confirmed while a bus was actually driving)

`deliveryWithBus/location` during a run:

```json
{"studentLocation": null,
 "busLocation": [{"busId": "<uuid>",
                  "location": {"type": "Feature",
                               "geometry": {"type": "Point",
                                            "coordinates": [50.79969, 4.34165]},
                               "properties": {"name": "Unknown place"}},
                  "lastLocatedTime": "2026-09-21T05:35:44+0000",
                  "actual": true}],
 "schoolLocation":  [{"type": "Feature", "geometry": {...}, "properties": {...}}],
 "stationLocation": [{"type": "Feature", "geometry": {...}, "properties": {...}}]}
```

**The coordinate order is `[lat, lng]`, not GeoJSON's `[lng, lat]`** - despite
the `"type": "Feature"` wrapper. Verified against a known school address. Read
the spec way, both values stay in range and the bus silently appears thousands
of kilometres away, so this is worth a dedicated test. `util.extract_latlon`
prefers the observed order and only falls back to the spec order when the first
value cannot be a latitude (|x| > 90).

Each of the three location keys is a **list**, not an object. `busLocation`
entries also carry `lastLocatedTime` and `actual` (whether the fix is current).

Once a run is live the agenda entry fills in the fields that are `null` at rest:

- `busId`, `activeRouteId` - uuids of the running bus and route
- `studentState` - `IS_NOT_ON_BOARD`, and `IS_ON_BOARD` after boarding
- `routeState` - `ON_TIME` (the backend's own punctuality verdict; other
  values not yet observed)
- `online: true`

No separate delay field appears in this payload; `routeState` is the signal.

Once the child boards:

- `checkInTime` is filled - but as a **bare time-of-day** (`"05:50:46+0000"`),
  unlike `startTime`, which is a full ISO datetime. Anchor it to the date of
  `startTime`, or it shows as a UTC clock time two hours off local.
- `studentState` flips to `IS_ON_BOARD` and `inOtherStop` may become `true`
- the location object's `"type"` changes from `"Feature"` to `"Embarked"`;
  only `geometry.coordinates` should be relied on, never `type`

When the run finishes:

- `checkOutTime` is filled (again a bare time-of-day), `routeState` becomes
  `COMPLETED`, `studentState` returns to `IS_NOT_ON_BOARD` and `online` goes
  back to `false`
- `busLocation` becomes `null` again - there is no last-known position to fall
  back on, so the tracker is simply unavailable outside runs

Full observed lifecycle of one run:

| phase | online | busLocation | studentState | routeState | timestamps |
| --- | --- | --- | --- | --- | --- |
| at rest | false | null | null | null | none |
| on route | true | list | IS_NOT_ON_BOARD | ON_TIME | - |
| boarded | true | list | IS_ON_BOARD | ON_TIME | checkInTime |
| finished | false | null | IS_NOT_ON_BOARD | COMPLETED | + checkOutTime |
