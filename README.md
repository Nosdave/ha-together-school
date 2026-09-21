# Together School – Home Assistant integration

Track your child's **school bus live position** and **check-in / check-out** from
the *Together School* app (Together School Ltd) in Home Assistant.

Together School is used by the APEEE transport services of the European Schools
in Brussels and by international schools worldwide. Each school is a separate
tenant on its own subdomain, so the integration works for any of them by
changing the school code.

> **Status: beta.** Unofficial and not affiliated with Together School Ltd or
> any APEEE. Nothing school-specific is baked in: the integration is told which
> deployment to talk to at setup, and all data comes from your own parent
> login.

## Entities

One device per child, discovered automatically from your parent account:

| Entity | What it gives you |
| --- | --- |
| `device_tracker.…_bus` | the bus position, on the HA map |
| `binary_sensor.…_on_bus` | on between check-in and check-out |
| `binary_sensor.…_missed_the_bus` | on when the school flagged a missed pickup |
| `sensor.…_bus_status` | `scheduled` / `on_route` / `on_board` / `completed` / `missed` / `no_service`, with bus number and stop names as attributes |
| `sensor.…_scheduled_departure` | timestamp of the pickup |
| `sensor.…_scheduled_arrival` | timestamp of the arrival |

Timetable and live position come from different endpoints: the schedule is
available as soon as a school day is planned, while the map position only fills
in while a bus is actually driving. The API speaks UTC; the timestamp sensors
hand Home Assistant proper aware datetimes, so times render in your timezone.

## Installation

**HACS** – add this repository as a custom repository (type: Integration), then
install *Together School* and restart Home Assistant.

**Manual** – copy `custom_components/together_school` into your
`config/custom_components/` directory and restart Home Assistant.

Then: **Settings → Devices & Services → Add Integration → Together School**.

## Configuration

You need two things:

- **Your mobile phone number** – the one registered in your child's APEEE
  profile, in international format (e.g. `+32470000000`). This is the login
  identifier; the email address does **not** work.
- **Your password** – the same one you use in the app.

The **school code** is the subdomain of your school's Together School site -
the part before `.together-school.com`. Your transport office can tell you, and
it is visible in the address of the school's web portal.

The tenant id differs per deployment and is **discovered automatically** from
the school's public web config. You are only asked for it if that lookup fails.

### Your password is not stored

Signing in returns a long-lived access token. The integration exchanges your
password for that token once and then stores **only the token** – the password
is never written to the config entry.

If the token is ever rejected, Home Assistant raises its standard
re-authentication prompt and asks for the password again, once.

## Dashboards

Ready-to-paste examples live in [`examples/`](examples/):

- [`dashboard.yaml`](examples/dashboard.yaml) – the school-bus view: live map,
  status, and a check-in timeline for the day.
- [`dashboard-morning.yaml`](examples/dashboard-morning.yaml) – a combined
  morning view that puts the school bus next to your own commute page.
- [`automations.yaml`](examples/automations.yaml) – notifications for check-in,
  check-out, bus approaching home, and a missed pickup.

## Polling

By default the integration only polls during commute windows; outside those the
bus is not running anyway. The default windows are only a starting suggestion –
set your own under **Configure** on the integration (times are interpreted in
Home Assistant's timezone). This keeps the load on the school's backend to a
minimum – please keep it that way.

## How it works

The app talks to a plain REST backend at `https://<school_code>.together-school.com`,
one tenant per school.
Every request carries the raw access token in `Authorization` (no `Bearer`
prefix) plus `X-TenantID` and `Locale`, and every response is wrapped in a
`{"status": "SUCCESS", "data": …}` envelope.

The endpoints used are `/auth/signin`, `/auth/users/me`,
`/parents/{id}/pupils`, `/transportation/pupils/{id}/deliveryWithBus/location`
and `/pupils/{id}/combined/agenda/{date}`.

Full protocol notes, including the pitfalls (parent accounts are rejected on
`clientType: WEB`; `MOBILE` requires a non-empty `deviceToken`), are in
[`DEV_NOTES.md`](DEV_NOTES.md).

## Development

`tools/probe.py` is a dependency-free CLI that signs in and dumps the raw JSON
for your account – handy for checking credentials or seeing new fields:

```bash
python3 tools/probe.py --school <school_code> --login '+324XXXXXXXX' --mobile
```

It prompts for the password rather than taking it on the command line.

Tests need nothing but the standard library:

```bash
python3 tests/test_parsing.py
python3 tests/test_no_password_stored.py
python3 tests/test_runtime_contract.py
python3 tools/check_neutral.py   # no school-specific values committed
```

## Licence

MIT
