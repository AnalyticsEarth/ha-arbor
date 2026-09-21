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
