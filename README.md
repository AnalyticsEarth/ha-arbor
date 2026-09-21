# Arbor Education for Home Assistant

[![HACS: custom repository](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://hacs.xyz/docs/faq/custom_repositories)
[![Home Assistant 2024.11+](https://img.shields.io/badge/Home%20Assistant-2024.11%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/github/license/AnalyticsEarth/ha-arbor.svg?style=for-the-badge)](LICENSE)

A custom integration that brings your child's [Arbor](https://arbor-education.com)
Parent Portal data into Home Assistant: attendance, behaviour points, homework,
timetable, meal balance, grades and school notices.

One Home Assistant device is created per child, so a family with two children at
the same school gets two devices with their own entities.

> Arbor does not offer a guardian API. This integration signs in as you and reads
> the same pages the Parent Portal shows you — see [docs/PROTOCOL.md](docs/PROTOCOL.md).
> It is read-only: it never submits a payment, a consent, or a piece of homework.

## Requirements

- Home Assistant **2024.11** or newer
- An Arbor Parent Portal login (the email address your school has for you)
- Your school must have the Parent Portal switched on

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/AnalyticsEarth/ha-arbor` as an **Integration**
3. Install **Arbor Education**, then restart Home Assistant

### Manual

Copy `custom_components/arbor` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

**Settings → Devices & services → Add integration → Arbor Education**

Enter the email address and password you use at `login.arbor.sc`. If your account
covers children at more than one school you will be asked to pick one; add the
integration again for each additional school.

Polling defaults to every 30 minutes and can be changed in the integration's
options (minimum 10 minutes). Arbor updates a handful of times a day at most, and
both of its login endpoints rate-limit, so there is nothing to gain from polling
harder.

## Entities

Per child:

| Entity | Type | Notes |
| --- | --- | --- |
| *(the child)* | sensor | Named after the child, e.g. `sensor.amelia_example`. State is their name; **every** detail below is on it as an attribute |
| Attendance | sensor (%) | Attributes: present sessions, authorised/unauthorised absences, lates |
| Behaviour points | sensor | Net points. Attributes: positive, negative, recent incidents |
| Positive points / Negative points | sensor | The two sides on their own, for graphing |
| Assignments outstanding | sensor | Attributes: the full list with subject, due date, status |
| Assignments overdue | sensor | Outstanding work whose deadline has passed |
| Next lesson | sensor | Subject. Attributes: start, end, room, teacher, lessons today |
| Meal balance | sensor (£) | Attributes: every account Arbor shows |
| Notices | sensor | Count. Attributes: titles, dates and bodies |
| School today | binary_sensor | Whether anything is timetabled today |
| In lesson | binary_sensor | Whether a lesson is running right now |
| Overdue assignments | binary_sensor | On when anything is overdue |
| Timetable | calendar | Lessons and school events |
| Assignments | todo | Read-only; submitted work shows as completed |

Entities whose data your school does not publish stay `unknown` rather than
disappearing, so an automation referencing them never breaks.

### The summary entity

Each child's device has one entity named after the child itself, carrying the
whole picture as attributes — attendance, behaviour, assignments, timetable,
grades, balances and notices. It is there for dashboard cards and templates that
want everything in one place:

```yaml
type: markdown
content: >-
  {% set c = state_attr('sensor.amelia_example', 'attendance_percentage') %}
  Attendance {{ c }}% ·
  {{ state_attr('sensor.amelia_example', 'assignments_outstanding') }} assignments due
  ({{ state_attr('sensor.amelia_example', 'assignments_overdue') }} overdue) ·
  next: {{ state_attr('sensor.amelia_example', 'next_lesson') }}
```

Use the individual sensors for anything you want to **graph or trigger on** —
attributes are not kept in history, and the list-valued ones are explicitly
excluded from the recorder so they do not bloat your database.

### Siblings

Arbor's dashboard and calendar feeds show whichever child is currently selected,
so with more than one child the integration reads each child only from their own
profile page and the pages linked from it. That is deliberately cautious: it
means a sibling's attendance can never be reported against the wrong child, but
it also means a value your school only publishes on the dashboard will be
`unknown` for a multi-child account. Single-child accounts use every source.

## Example automations

Tell everyone about overdue homework after school:

```yaml
automation:
  - alias: Overdue homework reminder
    triggers:
      - trigger: time
        at: "17:00:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.amelia_assignments_overdue
        above: 0
    actions:
      - action: notify.family
        data:
          title: Homework
          message: >-
            {{ states('sensor.amelia_assignments_overdue') }} assignment(s) overdue:
            {{ state_attr('sensor.amelia_assignments_overdue', 'assignments')
               | map(attribute='title') | join(', ') }}
```

Top up the meal account before it runs dry:

```yaml
automation:
  - alias: Meal balance low
    triggers:
      - trigger: numeric_state
        entity_id: sensor.amelia_meal_balance
        below: 5
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            Amelia's meal balance is £{{ states('sensor.amelia_meal_balance') }}.
```

## Services

| Service | What it does |
| --- | --- |
| `arbor.refresh` | Fetch everything again now, for every configured account |
| `arbor.dump_page` | Return the raw JSON a portal page serves (response-only) |

`arbor.dump_page` is the tool for diagnosing an empty sensor. By default it
returns only the *structure* of what the page serves, which is safe to paste into
an issue:

```yaml
action: arbor.dump_page
data:
  config_entry_id: <from the integration's ⋮ menu → "Copy entry id">
  path: /guardians/student-ui/assignments/student-id/12345
```

```yaml
shape:
  success: true
  assignments:
    "<list>": 2
    "<of>":
      assignmentName: str[19]
      subjectName: str[7]
      dueDate: str[19](datetime)
      submissionStatus: str[9]
      grade: str[2]
```

Keys, nesting, list lengths and each value's type and format — and no values, so
your child's name, their teachers' names and any comments stay in Home Assistant.
That is everything needed to fix a parser.

Add `include_values: true` to get the payload itself. **That contains your
child's personal data; do not share it.**

## When a sensor is empty

Arbor's authenticated page structure varies by school and by Arbor release, and
the parser recognises data by shape and wording rather than by fixed key paths.
Two dialects are handled: pages that declare their own columns, and pages that
just return a list of records. If a value is still missing:

1. Check the integration's **Download diagnostics** — it lists which pages were
   scraped, which domains came back empty, and the shape of each discovered URL,
   with no personal data.
2. Run `arbor.dump_page` for the page that should hold the value, leaving
   `include_values` off.
3. Open an issue with both. Neither contains your child's data.

## When will it ask for my password again?

Almost never. The password is saved in the config entry at setup, reused on every
refresh, and never cleared — a failed update does not discard it.

Home Assistant only prompts for it again when **Arbor itself rejects the
credentials on three consecutive refreshes**. Arbor rejects a login while
rate-limiting and under load, and the saved password is almost never the real
cause, so a single rejection is logged and retried silently.

Nothing else can trigger the prompt. Not a timeout, not a slow portal, not a 500,
and not a page your school does not let guardians see — a forbidden endpoint is
remembered and skipped, because the credentials that reached it were plainly
valid. The integration enforces this structurally: authentication errors may only
be raised while logging in, and a test fails the build if any other code path
raises one.

If you genuinely change your Arbor password, the prompt appears within a few
refresh cycles; use **Reconfigure** on the integration to update it immediately.

## Privacy

- Credentials are stored in Home Assistant's config entry store, like every other
  integration, and are sent only to `login.arbor.sc` and your school's own
  `*.arbor.sc` host.
- Scraped data stays in Home Assistant. Nothing is sent anywhere else.
- Diagnostics deliberately report counts and shapes rather than children's data.
- Only URLs on your school's own Arbor host are ever followed, so a link inside a
  scraped page cannot redirect the integration off-tenant.

## Checking it against your own account

`tools/arbor_probe.py` runs the integration's own scraper from a terminal — same
login protocol, same page URLs, same parser, same orchestration — so a problem
can be diagnosed without restarting Home Assistant. Only the HTTP transport
differs; it uses the standard library, so there is nothing to install.

```bash
python3 tools/arbor_probe.py report --email you@example.com
```

It prompts for your password without echoing it. Set `ARBOR_PASSWORD` instead if
you prefer. There is deliberately **no `--password` flag**: it would be recorded
in your shell history.

If your account covers children at more than one school it asks which one, once,
and remembers your answer for next time:

```
This account covers 2 schools:
  1) Ightham Primary School (TN15 9DD)
  2) Wrotham School (TN15 7RD)
Which one? [1-2] 2
Remembering that choice; pass --school to change it.
```

The choice is kept in `~/.config/arbor-probe/schools.json` — the school URL only,
never your password. `--school` overrides it (a URL, a bare host, or a name
fragment such as `wrotham`), `$ARBOR_SCHOOL` sets a default, and
`--forget-school` clears it. When there is no terminal to ask, it lists the
schools and stops rather than guessing.

`$ARBOR_EMAIL` works the same way for `--email`.

Each school is a separate Arbor tenant with its own children, and a separate
config entry in Home Assistant.

```
── A… E…  (id 40219)
   attendance        96.4%
   behaviour net     114.0
   assignments       3 total, 2 outstanding, 1 overdue
   next lesson       Biology at 2026-09-22 09:00:00
   EMPTY             timetable
   pages scraped     7
     - assignments:Assignments
     ...
```

| Command | What it does |
| --- | --- |
| `report` | What every entity would show, plus which domains are empty |
| `pages` | The portal pages discovered for your account |
| `shapes` | The structure of **every** page a scrape reads — the one to send when data is missing. `--depth N` if you see `<max depth>` |
| `shape <path>` | One page's structure |
| `json <path>` | One `/format/json` endpoint |
| `login` | The three login steps and exactly what Arbor answers. Start here if a login that works in a browser is refused |
| `whoami` | **Start here when nothing works.** Proves whether Arbor considers the session logged in, then tries each homepage and endpoint |

`whoami` exists because every portal route answers an unauthenticated request
with the same `401` a forbidden one gives, so "the dashboard returned 401" does
not say which happened. It reports `logged_in` from Arbor itself and exits `3`
for a session problem, `4` when the session is fine but no homepage is on offer.

Output is **redacted by default**: names are masked and free text is replaced
with a type-and-length placeholder such as `str[14]`, so a result can be pasted
into an issue as-is. Add `--show-values` to see the real data on your own screen —
useful for confirming a parse is actually correct:

```
   sample assignments:
     · 'Macbeth Act 2 essay' subject='English' due=2026-09-18 status='Submitted' grade='B+'
```

## Development

Nothing outside the Home Assistant entity layer depends on a third-party
package, so the tests run on a plain Python install:

```bash
python3 -m unittest discover -s tests -v
```

The layering is what makes that possible:

| Module | Depends on | Tested |
| --- | --- | --- |
| `errors.py` | nothing | taxonomy, plus a static guard that only the login flow raises an auth error |
| `http_util.py` | nothing | URL building, response classification |
| `protocol.py` | the above | the login request/response sequence |
| `parser.py` | the above | both payload dialects, every extractor |
| `scraper.py` | the above | the whole pipeline, against a fake portal |
| `api.py`, `coordinator.py`, entities | aiohttp, Home Assistant | — |

`ArborScraper` takes its two fetchers as arguments, so `tests/test_scraper.py`
drives a complete guardian account from fixtures, and `tools/arbor_probe.py`
drives a real one over the standard library. A parser change is therefore
verifiable both ways without Home Assistant in the loop.

`tests/fixtures/pages.py` holds sample Arbor payloads in both dialects — pages
that declare their own columns, and pages that just return records. Adding a
fixture from your own school (with personal data removed; `arbor_probe.py shape`
gives you exactly that) is the single most useful contribution.

## Disclaimer

Not affiliated with, endorsed by, or supported by Arbor Education. Use it with
your own account, for your own children. Arbor may change the portal at any time
and break this integration.
