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

DEFAULT_SCAN_INTERVAL_MINUTES = 30
MIN_SCAN_INTERVAL_MINUTES = 10

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
CALENDAR_ENTRY_LIST_PATH = "/calendar-entry/list-static/format/json/"
NOTIFICATIONS_PATH = "/user-notification/get-notifications/format/json/"

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
    DATA_MEALS: ("meal", "lunch", "dinner", "catering", "account", "balance"),
    DATA_NOTICES: ("notice", "news", "bulletin", "message"),
    DATA_PAYMENTS: ("payment", "invoice", "shop", "trip", "club", "fee"),
    DATA_EXAMINATIONS: ("examination", "exam"),
    DATA_REPORT_CARDS: ("report card", "report", "reports"),
}

SERVICE_REFRESH = "refresh"
SERVICE_DUMP_PAGE = "dump_page"

ATTR_STUDENT = "student"
ATTR_PATH = "path"

MANUFACTURER = "Arbor Education"
