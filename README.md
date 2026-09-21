# Arbor Education for Home Assistant

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

`arbor.dump_page` is the tool for diagnosing an empty sensor:

```yaml
action: arbor.dump_page
data:
  config_entry_id: <from the integration's ⋮ menu → "Copy entry id">
  path: /guardians/home-ui/dashboard
```

## When a sensor is empty

Arbor's authenticated page structure varies by school and by Arbor release, and
the parser recognises tables and tiles by their wording. If a value is missing:

1. Check the integration's **Download diagnostics** — it lists which pages were
   scraped and which domains came back empty, without including personal data.
2. Run `arbor.dump_page` for the page that should hold the value.
3. Open an issue with the (redacted) output. Names, ids and comments are personal
   data — please remove them; the column captions and structure are the useful part.

## Privacy

- Credentials are stored in Home Assistant's config entry store, like every other
  integration, and are sent only to `login.arbor.sc` and your school's own
  `*.arbor.sc` host.
- Scraped data stays in Home Assistant. Nothing is sent anywhere else.
- Diagnostics deliberately report counts and shapes rather than children's data.
- Only URLs on your school's own Arbor host are ever followed, so a link inside a
  scraped page cannot redirect the integration off-tenant.

## Development

The parser has no third-party dependencies, so its tests run on a plain Python
install:

```bash
python3 -m unittest discover -s tests -v
```

`tests/fixtures/pages.py` holds sample Arbor component trees in both grid
dialects Arbor emits. Adding a fixture from your own school (with personal data
removed) is the single most useful contribution.

## Disclaimer

Not affiliated with, endorsed by, or supported by Arbor Education. Use it with
your own account, for your own children. Arbor may change the portal at any time
and break this integration.
