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
