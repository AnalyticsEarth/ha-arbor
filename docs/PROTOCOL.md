# How this integration talks to Arbor

Arbor has a partner API (`developers.arbor-education.com`) but it is granted to
schools and vendors, not to guardians, and it does not cover the Parent Portal.
This integration therefore uses the same endpoints the Parent Portal's own
JavaScript uses.

Everything below was read out of Arbor's **publicly served, unauthenticated**
front-end assets — the login page, `login.js`, `bootstrap.js` and the portal's
`arbor-fe.main.*.js` bundle. No account was used to discover any of it, so the
authenticated payload *shapes* are inferred rather than observed. That is why
`parser.py` matches on structure and wording instead of fixed key paths, and why
`arbor.dump_page` exists.

## 1. Find the school

The central login page does not know which school an email belongs to, so it
asks first:

```
POST https://login.arbor.sc/applications/search-by-email
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
Body:         {"email": "...", "password": "..."}

200 {"payload": [{"name": ..., "shortName": ..., "sisUrl": "school.uk.arbor.sc",
                  "postalCode": ..., "location": ...}, ...]}
```

An empty `payload` means the credentials were wrong — the endpoint validates
them. `429` means rate-limited. `sisUrl` may arrive without a scheme, so it is
run through `normalise_base_url()`.

Source: `https://login.arbor.sc/js/login.js` → `getSchoolsByEmail()`, and
`prepareUrl()` in `bootstrap.js`.

## 2. Log in to that school

```
POST https://<school>.uk.arbor.sc/auth/login?lang=en
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
Body:         {"items": [{"username": "...", "password": "..."}]}

200 {"success": true, "items": [{"logged_in": true, "session_id": "..."}]}
```

Source: `login.js` → `login()`.

## 3. Exchange the session id for a cookie

The login response is not enough on its own; the portal only becomes usable
after this redirect, which sets the `mis` session cookie:

```
GET https://<school>.uk.arbor.sc/?session=<session_id>&lang=en
```

Source: `login.js` → `redirectToSchool()`.

## 4. Read pages as JSON

The address bar shows a portal route inside the **query string**:

```
https://<school>.uk.arbor.sc/?/guardians/home-ui/dashboard
```

That is only how the single-page app represents its current route. Its loader
strips everything up to and including a `?` that is followed by `/`, then
fetches the result as an ordinary **path**, appending `format=javascript` to get
the page as a JSON component tree rather than the HTML shell:

```js
// arbor-fe.main.*.js, Loader.loadPage
var r = function (e) {
  var t = e.indexOf("?");
  return -1 !== t && "/" === e[t + 1] ? e.substring(t + 1) : e;
}(e);
// ... then Loader._requestContent(r, n):
var k = function (e) {
  return e.includes("format=javascript")
    ? e
    : e + (e.includes("?") ? "&" : "?") + "format=javascript";
}(e);
```

So the request this integration makes is:

```
GET https://<school>.uk.arbor.sc/guardians/home-ui/dashboard?format=javascript
```

**Not** `GET /?/guardians/home-ui/dashboard&format=javascript`. That form is
answered with the HTML application shell, whatever the route, so it looks like an
authentication failure when it is really a malformed request. Confirmed against a
live tenant while unauthenticated:

| Request | Response |
| --- | --- |
| `/guardians/home-ui/dashboard?format=javascript` | `application/json` — `{"success":false,"message":"User is not allowed to access mvc:default/..."}` |
| `/?/guardians/home-ui/dashboard&format=javascript` | `text/html` — the application shell |

The three portal homepages, all named in the same bundle, are:

| Route | Who sees it |
| --- | --- |
| `/guardians/home-ui/dashboard` | guardians / parents |
| `/students/home-ui/dashboard` | students |
| `/home-ui/index` | school staff |

### Only the login step can be an authentication failure

A dead session and a resource the account may not see are indistinguishable on
the wire: both arrive as a 403, or as the HTML shell with a 200. But logging in
is what validates credentials -- it returns `logged_in: true` with a session
cookie, or it fails outright. So once a fresh login has succeeded, any later
denial is about the *resource*, never the password.

The client therefore re-authenticates once on a denial, and after that treats a
denial as "Arbor does not offer this to this account", skipping it. Only the
login handshake raises an authentication error. Getting this wrong takes the
whole integration down and asks the user to re-enter a password that was never
wrong -- for example `/calendar-entry/list-static`, which guardians cannot call
at all.

Refusals are remembered for the rest of the day so they are not re-requested on
every refresh, with date segments masked so the key survives midnight.

### A refused login: three different things, one status

`/auth/login` answers all of these with HTTP 200 and `success: false`, and only
the message separates them. Observed verbatim from a live account:

| Arbor says | Means | Right response |
| --- | --- | --- |
| "The username or password you entered is incorrect..." | wrong credentials | check the password, **and the school** |
| "You've exceeded the limit for unsuccessful logins. Please try again in a couple of minutes." | throttled | wait; the password may be perfectly good |
| `login_form_enabled: false` | locked | reset the password; retrying will not help |

Only the first is an authentication failure. A throttle is transient, so it must
not count towards prompting the user to re-enter a password that was always
correct. The wording is matched against **Arbor's own message**, never against
our generated advice -- the lockout text mentions "too many attempts" and would
otherwise be misread as a throttle.

### A refused page is HTTP 200 with a JSON error

A page the account may not see comes back `200` with
`{"success": false, "message": "User is not allowed to access ..."}`, so the
client treats a falsy `success` as "skip this page" rather than parsing the error
object as content.

### Important: an expired session returns HTTP 200

An unauthenticated request is answered with the HTML application shell and a
`200`, not a `401`. Status codes alone cannot detect a dead session, so
`looks_like_html()` inspects the body and triggers one re-login.

## 5. Direct JSON endpoints

Arbor also exposes controller actions that return JSON directly. These come from
the frozen URL-constants object in the bundle:

| Path | Contents |
| --- | --- |
| `/auth/current-user-settings/format/json` | signed-in user and school |
| `/navigation/main-menu/format/json` | the whole menu tree, with URLs |
| `/widget-data/get-notices/format/json` | school notices |
| `/widget-data/get-calendar-data/format/json/` | calendar / timetable data |
| `/calendar-entry/list-static/format/json/` | calendar entries |
| `/user-notification/get-notifications/format/json/` | user notifications |
| `/auth/logout` | ends the session |

The `/format/json` suffix is Arbor's general convention for asking a controller
action for JSON.

## 5a. Observed guardian responses

From a live guardian account at one school, for the record:

- `/auth/current-user-settings/format/json` reports `logged_in`, a `display_name`,
  `user_type`, `isParentPortalOrStudentPortal`, and a **`jwt`**.
- `/guardians/home-ui/dashboard` answers with
  `{type, content, helpCentreUrl, navigation}` -- the page body is under
  `content`, not at the top level.
- `/students/home-ui/dashboard` is **403** for a guardian, as expected; it is the
  student portal. The guardian and staff homepages both answer.
- `/navigation/main-menu/format/json` and
  `/widget-data/get-notices/format/json` both answer.

That `jwt` is worth knowing about: the front-end bundle attaches it as
`Authorization: Bearer <jwt>` when present. Nothing has needed it so far --
every route above answers with the session cookie alone -- so this integration
does not send it. If a data endpoint ever returns 401 while the session is
demonstrably valid, that is the first thing to try.

## 5b. Guardian pages return a layout, not data

This is the single most important thing about Arbor's newer guardian routes, and
the reason a scrape can fetch every page successfully and extract nothing.

A page such as `/guardians/student-ui/assignments/student-id/<n>` answers with a
component tree and **no data in it**:

```
type: "page"
content: [ { xtype: "mis-layoutcolumn", content: [
             { xtype: "new-kpi-panel",       props: { url: "/..." } } ] } ]
subNav:  { props: { props: { treeData: { items: [ ... ] } } } }
```

The data is behind the `url` (or `pageUrl`, on a `mis-button-load-page` whose
`role` is `load-page`) in a component's `props`. The front end fetches that
separately, so anything reading only the page sees a layout. The scraper follows
those paths, up to `MAX_CONTENT_DEPTH` levels and within a per-child request
budget.

Not every page works that way. The behaviour page carries its data inline as
`mis-section` → `mis-subsection` → `mis-property-row` components, each row
holding a `fieldLabel` and a `value` inside `props`. And the "Printable
Timetable" page is a PDF download form (`role: form-download-pdf`), so it holds
no timetable data at all -- a guardian timetable has to come from the calendar
feeds instead.

### What each guardian page actually holds

Observed at one secondary school, and the shapes the parser is written against:

| Page | Holds |
| --- | --- |
| Assignments | a `new-kpi-panel` whose content is a list of tiles: `{title, description, mainValue, url}`. The number is **`mainValue`**, and each tile's `url` is the detail page |
| Attendance | only a `mis-button-load-page` for "Log Absence". The percentage arrives as a KPI tile, not from this page |
| Behaviour | `mis-property-row` components inline, each `props.fieldLabel` a **date** and `props.value` an **HTML** description of the incident |
| Calendar | a `mis-calendar-calendar` with `referenceObjectId` and `referenceObjectTypeId` |
| Printable Timetable | a PDF download form; no timetable data |
| My Account | the guardian's own change-password form, not a meal balance |

Two consequences worth stating:

- A KPI panel mixes domains, so an assignments page's tiles can carry the
  attendance percentage. Metric-driven extraction therefore reads every tree
  fetched for a child rather than only the page filed under that domain.
- An action URL answers with `type: "slideover"` -- the Log Absence form, the
  change-password form. Those carry nothing about the child, so a caption
  beginning "Log ", "Change ", "Pay " and so on is not followed, and any payload
  that comes back as a slideover is discarded.

### Where a guardian's data actually lives

Confirmed against a live account. The short version: **almost none of it is on
the page named after it.** The dashboard's own links are the map.

| Data | Source |
| --- | --- |
| Attendance % | `/guardians/student/kpis/id/<id>/` — captioned "Attendance (2026/2027)", the number inside rendered HTML |
| Behaviour totals | the same KPI list — "Positive/Negative/Neutral Behavioural Incidents - this term". Published as **incident counts**, not points |
| Behaviour incidents | the behaviour page, under "Positive/Negative/Neutral Incidents Breakdown" |
| Assignments due | the subnav page "Assignments that are due" (and the dashboard): rows reading `9En4: Term 1 - Task 1 (Due 24 Sep 2026)` with the status alongside |
| Assignment subject, marking and instructions | the link on each of those rows, `/guardians/student-ui/schoolwork-overview/schoolwork-id/<n>/student-id/<id>/…` |
| Meal balance | the **dashboard**, section "Accounts": row description `Balance: £4.15` |
| Timetable | `/guardians/widget-data/get-calendar-data/student-id/<id>/` — events with `start_datetime`, `end_datetime`, `title`, `location` |
| The child's name | the caption of `/guardians/student-ui/overview/id/<id>` |

Three traps in that list:

- Both per-child endpoints must be fetched as **plain JSON**. Requesting either
  as a page, with `format=javascript`, returns a **500**.
- The attendance *page* holds nothing but a "Log Absence" button, and the
  assignments *landing* page holds only counts — its **subnav siblings** hold the
  lists. Looking for the data where its name suggests it should be wastes a lot
  of time.
- A row's link is not decoration. Following it is the only way to the subject,
  the marking scheme and the task itself.

### Reading a property row

`mis-property-row` is the workhorse of the guardian portal, and three separate
things on it carry meaning:

| Where | Holds |
| --- | --- |
| `props.fieldLabel` | the field name on a detail page (`Due`, `Course`, `Marking`), or the **date** on a behaviour incident |
| `props.value` | the value — as HTML, which may itself be several labelled fields |
| `props.description` | a status alongside, e.g. `Waiting for student to submit`, `Balance: £4.15` |

A multi-field value is one `<div>` per field, each `<b>Label:</b> value`:

```html
<div><span class="mis-dark-orange"><b>Behaviour:</b> Motivation</span></div>
<div><span class="mis-dark-orange"><b>Narrative:</b> Good work completed in lesson</span></div>
<div><span class="mis-dark-orange"><b>Recorded by:</b> Mr Fuller</span></div>
<div><span class="mis-dark-orange"><b>Event:</b> Maths KS4: 9Ma3</span></div>
```

Flattening that to text is lossy: an incident with no narrative emits
`<b>Narrative:</b> ` and the empty value runs straight into the next label. The
fields are read from the markup instead — `parser.parse_labelled_html`.

### Two headings, not one

`mis-subsection` contains the word "section", so a naive `"section" in xtype`
check lets the inner heading overwrite the outer one. That loses the only thing
that says whether an incident counts for or against the child:

```
mis-section    "Positive Incidents"          <- the polarity
  mis-subsection "Positive Incidents"        <- three totals, labelled
                                                Lifetime / 2026/2027 / Autumn
  mis-subsection "Positive Incidents Breakdown"  <- one row per incident
```

Two consequences worth stating plainly:

- **Each total appears three times, for different periods.** 129 positive
  incidents and 35 positive incidents are both true on the same page. The
  academic-year figure is the one the KPI panel shows, so that is the one a
  sensor reports.
- **Identical rows are not duplicates.** Two "Respect" incidents on the same day,
  from the same teacher, in the same lesson are two incidents. Deduplicating
  property rows by content undercounted a term by four.

### No behaviour points are published to a guardian

Worth stating because it looks like a gap in this integration and is not. At
Wrotham the guardian portal publishes behaviour **types** and **incident counts**
and no points at all:

- the KPI tile is captioned "Positive Behavioural Incidents - this term", value
  `35`, with no unit;
- the behaviour page's totals read "35 positive incidents";
- each incident row's payload has exactly two fields, `fieldLabel` (the date) and
  `value` (the four labelled fields), with no numeric field hidden behind them;
- nothing anywhere in the behaviour payload mentions a point;
- the guardian main menu has four items, and the whole guardian navigation — 20
  entries — has no rewards or points page. Neither the Top-Ups dashboard nor the
  school shop mentions one.

So `points` stays `None` on each incident rather than being inferred. A school
that does publish them is still read: `_POINTS_RE` matches only a number the text
itself calls a point, and the sign comes from the section heading.

### Attendance By Date: one row per register

`/guardians/student-ui/attendance-by-date/student-id/<id>` lists every
registration session, grouped into weeks. Three things on the row matter:

| Where | Holds |
| --- | --- |
| `props.fieldLabel` | the date and register: `21 Sep 2026 AM` |
| `props.description` | the mark **in words**: `Present AM`, `No Mark`, `Any Other Unavoidable Cause` |
| `props.value` | the code where there is one (`Y7`, `L`), otherwise a coloured **icon** or `-` |

A present mark is drawn as a tick, so `value` flattens to no text at all. Any
filter requiring a row to have text throws away almost every session — 24 of 28
in a term here. The description is the readable source.

The arithmetic is worth knowing: 28 listed sessions, minus two `Y7` and two
unmarked, is the 24-session total the summary page states. A **Y code** means the
child could not attend, and the session counts neither for them nor against them,
so it belongs in neither the numerator nor the denominator.

Marks can only be classified by the school's own wording, so anything
unrecognised is kept verbatim with a status of `other` rather than guessed at.

### A download button is not page content

The Attendance By Date page carries a PDF certificate button. Verbatim from a
live tenant:

```json
{
  "componentName": "Arbor.button.DownloadFile",
  "xtype": "mis-button-download-file",
  "props": {
    "text": "Attendance Certificate",
    "icon": "file-pdf",
    "role": "download-file",
    "pageUrl": "/guardians/student/download-attendance-certificate/student-id/<id>/academic-year-id/16"
  }
}
```

The caption is the innocuous "Attendance Certificate", so the action-caption
filter does not catch it; the component's own `xtype`, `role`, `componentName`
and `icon` all say what it is, and those are what `extract_content_urls` checks.
A path naming a `download`/`export` segment, or ending in a file extension, is a
backstop for a school whose component is not labelled.

Following it fetched the PDF. `aiohttp`'s `response.text()` then raised
`UnicodeDecodeError` — not one of the errors a page fetch is allowed to fail
with, so instead of skipping one page it ended the refresh and took every
entity for every child with it. The HTTP layer now reads bytes, refuses a
non-textual content type as "not available", and decodes with replacement so a
single bad byte costs one page rather than the update.

**The probe hid this.** Its `_decode` used `errors="replace"`, so the same PDF
came back as merely "unparseable JSON" while a real refresh crashed. A test
harness that is more forgiving than the code it stands in for cannot reproduce
what Home Assistant sees, so it now applies the same content-type check.

### KPI tiles state what they compare against

Each tile in `/guardians/student/kpis/id/<id>/` carries secondary figures in its
rendered HTML, one per `...-barchart-chart` block. Two shapes:

- **attendance** — a bar whose `title` is the figure, and a sibling `<label>`
  giving the caption: `Year` 100%, `Last 4 weeks` 96%;
- **behaviour** — no bar, and the label reads `Last term: 32 incidents`.

The previous term's figure is on the tile and nowhere else: the behaviour page
states this term, this year and the child's lifetime, but not the one before.

### A link is not a value

Arbor renders a filter as a labelled row whose value is a path:

```
Meals -> /guardians/customer-account-ui/top-ups-dashboard/student-id/1879/customer-account-id/5964/term-id/40
```

`parse_currency` used to fall through to "any bare number", which reported the
child's own id as a balance of £1879. It now refuses a path outright; the
bare-number fallback survives only for schools that print a balance with no
currency symbol.

### Classifying a page by its route

A caption is configured per school and can be as bare as "By Date". The route
behind it is Arbor's own: `/guardians/student-ui/attendance-by-date/`.
`classify_pages` matches the caption first and falls back to the path, which is
how the by-date page is found at all.

### Pages a guardian is offered that this integration does not read

From the subnav of any per-child page: Trips, Report Cards, Active Payments,
Invoices, Credit Notes and the school shop (Products). Report cards are listed
with a title, a date and a link, but the card body is not served as JSON — its
only content URL is the child's profile — so the contents are not reachable this
way.

Those links live in `subNav.props.props.treeData.items[].fields`, which
`find_links` does not walk, so they are not discovered from the navigation; the
pages that do get read are the ones linked from a page's own body.

The calendar feed returns **today only**. A date range presumably narrows it, but
that has not been established, so the timetable covers the current day.

### The calendar is a POST, and not to the widget endpoint

Two different things serve calendar data, and only one of them serves a
timetable page.

The **homepage widget** GETs
`/widget-data/get-calendar-data/format/json/object-id/<id>/object-type-id/<typeId>/`.
For a guardian that answers `{"items": [], "success": true}` -- and asking it
about a particular student is refused outright.

The **calendar page** (`mis-calendar-calendar`) POSTs to
`/calendar-entry/list-static/format/json/`. From `Mis.calendar.Abstract.load` in
the ExtJS bundle:

```js
var o = {action_params: {view: e, startDate: t, endDate: r, filters: []}};
n.referenceObjectTypeId && n.referenceObjectId && o.action_params.filters.push({
  field_name: "object",
  value: {_objectTypeId: n.referenceObjectTypeId, _objectId: n.referenceObjectId}
});
```

and it reads the answer from `items[0].fields.response.value`. The component's
props carry the two ids, so the filter is built from the child's own page.

### The calendar feed needs its object

Calling `/widget-data/get-calendar-data/format/json/` bare returns
`{"items": [], "success": true}` -- not an error, just nothing. The widget asks
per object, and the bundle shows exactly how:

```js
e = t.url ? t.url
          : WIDGET_DATA.GET_CALENDAR_DATA + "object-id/" + n + "/object-type-id/" + a + "/"
```

So with the calendar component's props:

```
/widget-data/get-calendar-data/format/json/object-id/<referenceObjectId>/object-type-id/<referenceObjectTypeId>/
```

That path is scoped to the object the child's own page named, so it is safe to
use even for a guardian with siblings -- unlike the bare feed.

### Navigation fields are wrapped

`subNav`'s tree carries each field as an object, not a string:

```json
{"fields": {"text": {"value": "Behaviour"},
            "url":  {"value": "/guardians/behaviour-ui/student-behaviour/student-id/1879"}}}
```

Reading only string URLs therefore missed every per-student navigation route.

## 6. Why discovery rather than hard-coded URLs

Guardian sub-pages (attendance, behaviour, assignments, and so on) are rendered
server-side and their URLs carry the child's id, for example
`/guardians/attendance/index/student-id/40219`. Those URLs are not in the
JavaScript bundle — Arbor emits them per account, and which ones exist depends
on what each school has switched on.

The coordinator therefore:

1. reads the guardian dashboard and the main menu;
2. finds children by looking for links carrying a student id;
3. fetches each child's profile page;
4. files the links it finds there into data domains by their caption
   (`DOMAIN_KEYWORDS` in `const.py`);
5. fetches those pages and hands them to the parser.

Discovered paths are cached per child so the discovery cost is paid once rather
than on every poll, and at most `MAX_PAGES_PER_STUDENT` pages are fetched per
child per refresh.

### Keeping siblings apart

Guardian-wide pages — the dashboard, the main menu and the calendar feeds — show
whichever child the portal currently has selected. Reading them as a particular
child's data is only safe when the guardian has one child.

So when there is more than one child:

- only that child's own profile page and the pages linked from it are mined for
  data;
- a discovered URL carrying a `student-id` that is not theirs is discarded
  (`filter_pages_for_student`);
- a discovered URL carrying no id at all is discarded too, because it resolves
  against the selected child;
- the shared calendar endpoints are skipped entirely, and the timetable comes
  from the child's own timetable page.

With a single child all of those sources are used, since there is nothing to
confuse them with.

## Limits worth knowing

- **Inferred payload shapes.** The parser was validated against fixtures built
  from Arbor's component conventions, not against a real authenticated response.
  Use `arbor.dump_page` to capture what your school actually serves.
- **No public API contract.** Arbor can change any of this without notice.
- **Rate limiting.** Both login endpoints return `429` when hit too often. The
  minimum poll interval is 10 minutes for this reason.
- **Read-only.** Nothing here writes to Arbor. The assignments to-do list does
  not hand work in, and no payment or consent action is ever submitted.
