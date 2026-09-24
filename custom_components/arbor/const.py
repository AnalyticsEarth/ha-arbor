"""Constants for the Arbor Education integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "arbor"

# --- Config entry keys -------------------------------------------------------

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"
CONF_SCHOOL_NAME = "school_name"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_CALENDAR_DAYS = "calendar_days"

DEFAULT_SCAN_INTERVAL_MINUTES = 30
MIN_SCAN_INTERVAL_MINUTES = 10

# How many days of timetable to fetch, today included. Arbor's guardian calendar
# serves exactly one day per request and ignores every range parameter tried, so
# this is also the number of requests it costs per child per refresh.
DEFAULT_CALENDAR_DAYS = 7
MIN_CALENDAR_DAYS = 1
MAX_CALENDAR_DAYS = 21

# Arbor is a school MIS, not a real-time system. Polling harder than this only
# adds load to the school's tenant without surfacing anything new.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

# --- Arbor endpoints ---------------------------------------------------------
# Derived from Arbor's own front-end bundle; see docs/PROTOCOL.md.

ARBOR_LOGIN_HOST = "https://login.arbor.sc"
SCHOOL_SEARCH_PATH = "/applications/search-by-email"

AUTH_LOGIN_PATH = "/auth/login"
AUTH_LOGOUT_PATH = "/auth/logout"
CURRENT_USER_SETTINGS_PATH = "/auth/current-user-settings/format/json"
MAIN_MENU_PATH = "/navigation/main-menu/format/json"
NOTICES_PATH = "/widget-data/get-notices/format/json"
CALENDAR_DATA_PATH = "/widget-data/get-calendar-data/format/json/"
# The calendar page's own source. It is a POST carrying the view, the date range
# and an object filter -- see protocol.calendar_request_body. A GET with the ids
# in the path is refused; that form belongs to the homepage widget only.
CALENDAR_ENTRY_LIST_PATH = "/calendar-entry/list-static/format/json/"
NOTIFICATIONS_PATH = "/user-notification/get-notifications/format/json/"

# Per-child endpoints, reached from the dashboard's own links. Both answer as
# plain JSON; asking for either as a page (with format=javascript) returns a 500.
STUDENT_KPIS_PATH = "/guardians/student/kpis/id/{student_id}/"
GUARDIAN_CALENDAR_PATH = (
    "/guardians/widget-data/get-calendar-data/student-id/{student_id}/"
)
# The same feed for one named day. Without the date segment it answers with today
# only; `start-date`/`end-date`, `startDate`/`endDate`, `view/week`, `num-days`
# and query-string forms were all tried against a live tenant and all returned
# today regardless. A day at a time is the only range control Arbor offers.
GUARDIAN_CALENDAR_DAY_PATH = (
    "/guardians/widget-data/get-calendar-data/student-id/{student_id}/date/{date}/"
)

GUARDIAN_DASHBOARD_PAGE = "/guardians/home-ui/dashboard"
STUDENT_DASHBOARD_PAGE = "/students/home-ui/dashboard"
STAFF_HOME_PAGE = "/home-ui/index"

# Query flag that makes Arbor return a page as a JSON component tree instead of
# the HTML application shell.
FORMAT_JAVASCRIPT = "format=javascript"

# --- Data domains ------------------------------------------------------------

DATA_ATTENDANCE = "attendance"
DATA_BEHAVIOUR = "behaviour"
DATA_ASSIGNMENTS = "assignments"
DATA_TIMETABLE = "timetable"
DATA_PROGRESS = "progress"
DATA_MEALS = "meals"
DATA_NOTICES = "notices"
DATA_PAYMENTS = "payments"
DATA_EXAMINATIONS = "examinations"
DATA_REPORT_CARDS = "report_cards"

ALL_DATA_DOMAINS = (
    DATA_ATTENDANCE,
    DATA_BEHAVIOUR,
    DATA_ASSIGNMENTS,
    DATA_TIMETABLE,
    DATA_PROGRESS,
    DATA_MEALS,
    DATA_NOTICES,
    DATA_PAYMENTS,
    DATA_EXAMINATIONS,
    DATA_REPORT_CARDS,
)

# Keywords used to classify a discovered portal page into one of the domains
# above. Order matters: the first domain with a matching keyword wins.
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    DATA_ATTENDANCE: ("attendance", "absence", "presenoldeb"),
    DATA_BEHAVIOUR: ("behaviour", "behavior", "conduct", "ymddygiad"),
    DATA_ASSIGNMENTS: ("assignment", "homework", "coursework", "gwaith cartref"),
    DATA_TIMETABLE: ("timetable", "calendar", "schedule", "amserlen"),
    DATA_PROGRESS: ("progress", "attainment", "grade", "assessment", "cynnydd"),
    # Not a bare "account": "My Account" is the guardian's own settings page,
    # which is a change-password form rather than a meal balance.
    DATA_MEALS: (
        "meal",
        "lunch",
        "dinner",
        "catering",
        "balance",
        "top up",
        "topup",
        "dinner money",
    ),
    DATA_NOTICES: ("notice", "news", "bulletin", "message"),
    DATA_PAYMENTS: ("payment", "invoice", "shop", "trip", "club", "fee"),
    DATA_EXAMINATIONS: ("examination", "exam"),
    DATA_REPORT_CARDS: ("report card", "report", "reports"),
}

# hass.data key holding, per config entry, how many consecutive refreshes Arbor
# has rejected the stored credentials on. Outlives the coordinator so a setup
# retry loop cannot reset it.
DATA_REJECTIONS = "credential_rejections"

SERVICE_REFRESH = "refresh"
SERVICE_DUMP_PAGE = "dump_page"

ATTR_STUDENT = "student"
ATTR_PATH = "path"
ATTR_INCLUDE_VALUES = "include_values"

MANUFACTURER = "Arbor Education"
