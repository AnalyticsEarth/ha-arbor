"""Representative Arbor component trees.

These mirror the shapes Arbor's front end builds -- ExtJS style grids with a
``columns`` definition plus a ``store``, dashboard tiles as label/value pairs,
and calendar endpoints returning flat event lists. Two dialects of each shape
are included on purpose, because Arbor is not consistent between page types:
grids sometimes key rows by ``dataIndex`` and sometimes emit positional arrays.
"""

from __future__ import annotations

GUARDIAN_DASHBOARD = {
    "success": True,
    "items": [
        {
            "xtype": "container",
            "title": "My Children",
            "items": [
                {
                    "xtype": "button",
                    "text": "Amelia Example",
                    "url": "/guardians/student-profile/index/student-id/40219",
                },
                {
                    "xtype": "button",
                    "text": "View Student Profile",
                    "url": "/guardians/student-profile/index/student-id/40219",
                },
                {
                    "xtype": "button",
                    "text": "Oliver Example",
                    "url": "/guardians/student-profile/index/student-id/40855",
                },
            ],
        },
        {
            "xtype": "kpiPanel",
            "title": "Statistics",
            "items": [
                {"xtype": "kpi", "label": "Attendance this year", "value": "96.4%"},
                {"xtype": "kpi", "label": "Positive behaviour points", "value": "128"},
                {"xtype": "kpi", "label": "Negative behaviour points", "value": "-14"},
                {"xtype": "kpi", "label": "Meal account balance", "value": "£12.45"},
            ],
        },
        {
            "xtype": "navigation",
            "items": [
                {"text": "Attendance", "url": "/guardians/attendance/index/student-id/40219"},
                {"text": "Behaviour", "url": "/guardians/behaviour/index/student-id/40219"},
                {"text": "Assignments", "url": "/guardians/assignments/index/student-id/40219"},
                {"text": "Calendar", "url": "/guardians/calendar/index/student-id/40219"},
                {"text": "Progress", "url": "/guardians/progress/index/student-id/40219"},
                {"text": "Meals", "url": "/guardians/meals/index/student-id/40219"},
                {"text": "Arbor Help Centre", "url": "https://support.arbor-education.com"},
            ],
        },
    ],
}

# Grid keyed by dataIndex, the common ExtJS dialect.
ASSIGNMENTS_PAGE = {
    "items": [
        {
            "xtype": "grid",
            "title": "Assignments",
            "columns": [
                {"text": "Assignment", "dataIndex": "name"},
                {"text": "Subject", "dataIndex": "subject"},
                {"text": "Due date", "dataIndex": "dueDate"},
                {"text": "Status", "dataIndex": "status"},
                {"text": "Mark", "dataIndex": "mark"},
                {"text": "Set by", "dataIndex": "staff"},
            ],
            "store": {
                "data": [
                    {
                        "name": "Photosynthesis worksheet",
                        "subject": "Biology",
                        "dueDate": "2026-09-25",
                        "status": "Not submitted",
                        "mark": "",
                        "staff": "Mrs J Okafor",
                    },
                    {
                        "name": "Macbeth Act 2 essay",
                        "subject": "English",
                        "dueDate": "18/09/2026",
                        "status": "Submitted",
                        "mark": "B+",
                        "staff": "Mr T Hale",
                    },
                    {
                        "name": "Quadratic equations problem set",
                        "subject": "Mathematics",
                        "dueDate": "Mon 14 Sep 2026",
                        "status": "Not submitted",
                        "mark": "",
                        "staff": "Ms R Patel",
                    },
                ]
            },
        }
    ]
}

# Grid emitting positional rows and plain-string column captions.
BEHAVIOUR_PAGE = {
    "items": [
        {
            "xtype": "panel",
            "title": "Behaviour",
            "items": [
                {"label": "Positive points", "value": "128"},
                {"label": "Negative points", "value": "14"},
            ],
        },
        {
            "xtype": "grid",
            "title": "Behaviour incidents",
            "columns": ["Date", "Type", "Points", "Subject", "Recorded by", "Comment"],
            "rows": [
                [
                    "21/09/2026",
                    "Positive - Excellent work",
                    "2",
                    "Biology",
                    "Mrs J Okafor",
                    "Outstanding contribution in class",
                ],
                [
                    "18/09/2026",
                    "Negative - Late to lesson",
                    "-1",
                    "Mathematics",
                    "Ms R Patel",
                    "Arrived 8 minutes late",
                ],
            ],
        },
    ]
}

ATTENDANCE_PAGE = {
    "items": [
        {
            "xtype": "panel",
            "title": "Attendance summary",
            "items": [
                {"label": "Attendance", "value": "96.4%"},
                {"label": "Present sessions", "value": "241"},
                {"label": "Authorised absences", "value": "6"},
                {"label": "Unauthorised absences", "value": "2"},
                {"label": "Late sessions", "value": "3"},
            ],
        }
    ]
}

TIMETABLE_PAGE = {
    "items": [
        {
            "xtype": "grid",
            "title": "Today's timetable",
            "columns": [
                {"text": "Period", "dataIndex": "period"},
                {"text": "Time", "dataIndex": "time"},
                {"text": "Subject", "dataIndex": "subject"},
                {"text": "Room", "dataIndex": "room"},
                {"text": "Teacher", "dataIndex": "teacher"},
            ],
            "rows": [
                {
                    "period": "1",
                    "time": "09:00 - 10:00",
                    "subject": "Biology",
                    "room": "S4",
                    "teacher": "Mrs J Okafor",
                },
                {
                    "period": "2",
                    "time": "10:05 - 11:05",
                    "subject": "Mathematics",
                    "room": "M2",
                    "teacher": "Ms R Patel",
                },
            ],
        }
    ]
}

CALENDAR_ENDPOINT = {
    "success": True,
    "items": [
        {
            "title": "Biology",
            "start": "2026-09-22T09:00:00",
            "end": "2026-09-22T10:00:00",
            "location": "S4",
            "teacher": "Mrs J Okafor",
        },
        {
            "title": "Mathematics",
            "start": "2026-09-22T10:05:00",
            "end": "2026-09-22T11:05:00",
            "location": "M2",
        },
        {"title": "INSET day", "date": "2026-09-28"},
    ],
}

PROGRESS_PAGE = {
    "items": [
        {
            "xtype": "grid",
            "title": "Progress",
            "columns": [
                {"text": "Subject", "dataIndex": "subject"},
                {"text": "Current grade", "dataIndex": "grade"},
                {"text": "Target grade", "dataIndex": "target"},
                {"text": "Assessment period", "dataIndex": "period"},
            ],
            "store": {
                "data": [
                    {
                        "subject": "Biology",
                        "grade": "7",
                        "target": "8",
                        "period": "Autumn 1",
                    },
                    {
                        "subject": "English",
                        "grade": "6",
                        "target": "6",
                        "period": "Autumn 1",
                    },
                ]
            },
        }
    ]
}

MEALS_PAGE = {
    "items": [
        {
            "xtype": "grid",
            "title": "Accounts",
            "columns": [
                {"text": "Account", "dataIndex": "name"},
                {"text": "Balance", "dataIndex": "balance"},
            ],
            "rows": [
                {"name": "Meals", "balance": "-£3.20"},
                {"name": "Trips", "balance": "£15.00"},
            ],
        }
    ]
}

NOTICES_ENDPOINT = {
    "items": [
        {
            "title": "Year 9 parents' evening",
            "body": "<p>Bookings open on <strong>Monday</strong>.</p>",
            "createdAt": "2026-09-19T16:30:00",
        },
        {
            "title": "Sports day postponed",
            "body": "Rescheduled to 3 October.",
            "createdAt": "2026-09-15T08:00:00",
        },
    ]
}

PROFILE_PAGE = {
    "items": [
        {
            "xtype": "infoPanel",
            "items": [
                {"fieldLabel": "Year group", "value": "Year 9"},
                {"fieldLabel": "Form group", "value": "9BQ"},
                {"fieldLabel": "Admission number", "value": "004219"},
            ],
        }
    ]
}


# A dashboard whose timetable widget links each lesson to a calendar entry.
# Every one of those URLs carries a bare `/id/<n>`, and captions like
# "Next lesson" read like two-word names -- which is how an early version of
# the parser turned three lessons into three children.
DASHBOARD_WITH_LESSON_LINKS = {
    "items": [
        {
            "xtype": "panel",
            "title": "Timetable",
            "items": [
                {
                    "text": "Previous lesson",
                    "url": "/guardians/calendar-entry/view-event/id/8814021",
                },
                {
                    "text": "Current lesson",
                    "url": "/guardians/calendar-entry/view-event/id/8814022",
                },
                {
                    "text": "Next lesson",
                    "url": "/guardians/calendar-entry/view-event/id/8814023",
                },
            ],
        },
        {
            "xtype": "panel",
            "title": "Notices",
            "items": [
                {"text": "Sports day", "url": "/guardians/news-story/view/id/55012"},
                {"text": "Term dates", "url": "/guardians/school-notice/view/id/55013"},
            ],
        },
        {
            "xtype": "container",
            "items": [
                {
                    "text": "Amelia Example",
                    "url": "/guardians/student-profile/index/student-id/40219",
                }
            ],
        },
    ]
}


# The other dialect: no column definitions at all, just records. Keys stand in
# for captions. Wrotham School's guardian pages are `-ui` routes that return
# payloads like this rather than declared ExtJS grids.
ASSIGNMENTS_RECORD_LIST = {
    "success": True,
    "assignments": [
        {
            "assignmentName": "Photosynthesis worksheet",
            "subjectName": "Biology",
            "dueDate": "2026-09-25T15:30:00",
            "submissionStatus": "Not submitted",
            "grade": None,
            "setByStaffName": "Mrs J Okafor",
        },
        {
            "assignmentName": "Macbeth Act 2 essay",
            "subjectName": "English",
            "dueDate": "2026-09-18T15:30:00",
            "submissionStatus": "Submitted",
            "grade": "B+",
            "setByStaffName": "Mr T Hale",
        },
    ],
}

BEHAVIOUR_RECORD_LIST = {
    "success": True,
    "behaviourIncidents": [
        {
            "incidentDate": "2026-09-21",
            "behaviourType": "Positive - Excellent work",
            "points": 2,
            "subjectName": "Biology",
            "recordedByStaffName": "Mrs J Okafor",
            "commentText": "Outstanding contribution",
        },
        {
            "incidentDate": "2026-09-18",
            "behaviourType": "Negative - Late to lesson",
            "points": -1,
            "subjectName": "Mathematics",
            "recordedByStaffName": "Ms R Patel",
            "commentText": "Eight minutes late",
        },
    ],
}

# A child's name as a record rather than a link caption.
STUDENT_RECORD_PAGE = {
    "success": True,
    "student": {
        "studentId": 40219,
        "firstName": "Amelia",
        "lastName": "Example",
        "yearGroupName": "Year 9",
        "registrationGroupName": "9BQ",
    },
}


# A guardian with one child, so the shared dashboard and calendar feeds are
# unambiguous and the whole pipeline can be exercised.
SINGLE_CHILD_DASHBOARD = {
    "success": True,
    "items": [
        {
            "xtype": "container",
            "items": [
                {
                    "text": "Amelia Example",
                    "url": "/guardians/student-profile/index/student-id/40219",
                }
            ],
        },
        {
            "xtype": "navigation",
            "items": [
                {"text": "Attendance", "url": "/guardians/attendance/index/student-id/40219"},
                {"text": "Log Absence", "url": "/guardians/absence/new/student-id/40219"},
                {"text": "Behaviour", "url": "/guardians/behaviour/index/student-id/40219"},
                {"text": "Assignments", "url": "/guardians/assignments/index/student-id/40219"},
                {"text": "Progress", "url": "/guardians/progress/index/student-id/40219"},
                {"text": "Meals", "url": "/guardians/meals/index/student-id/40219"},
            ],
        },
    ],
}

# Shaped after what a live guardian account returns: the keys are
# `display_name` and `organizationName`, not the camelCase names guessed first.
CURRENT_USER_SETTINGS = {
    "success": True,
    "items": [
        {
            "session_id": "0123456789abcdef01234567",
            "logged_in": True,
            "display_name": "Steven Example",
            "language": "en_GB",
            "applicationId": "uk_bkm_00000",
            "userId": 12345,
            "institutionType": "Sec",
            "isParentPortalOrStudentPortal": True,
            "country": "GBR",
            "organizationName": "Example School",
            "user_type": "guardian",
        }
    ],
    "action_params": [],
    "notifications": [],
}


# Wrotham School's actual architecture: a guardian data page returns a layout
# only. The real content sits behind a url in a component's props, and the
# per-student navigation lives in subNav with each field wrapped as
# {"value": ...}. Reading just the page finds no data whatever the parser does.
SHELL_PAGE_WITH_CONTENT_URL = {
    "type": "page",
    "content": [
        {
            "componentName": "Arbor.container.LayoutColumn",
            "type": "component",
            "content": [
                {
                    "componentName": "Arbor.button.LoadPage",
                    "props": {
                        "ui": "plain",
                        "text": "View all",
                        "role": "load-page",
                        "pageUrl": "/guardians/student-ui/assignments-content/student-id/1879",
                        "v2": True,
                    },
                    "xtype": "mis-button-load-page",
                }
            ],
            "xtype": "mis-layoutcolumn",
            "props": {
                "columnTitle": {"title": "Assignments", "tooltip": None},
                "formActions": [],
            },
        }
    ],
    "subNav": {
        "props": {
            "componentName": "Arbor.container.SubNav",
            "type": "component",
            "content": None,
            "props": {
                "id": "sub-nav",
                "treeData": {
                    "items": [
                        {
                            "expanded": False,
                            "fields": {
                                "text": {"value": "Behaviour"},
                                "url": {
                                    "value": "/guardians/behaviour-ui/student-behaviour/student-id/1879"
                                },
                                "selected": {"value": False},
                                "id": {"value": 2},
                            },
                            "leaf": True,
                        },
                        {
                            "expanded": False,
                            "fields": {
                                "text": {"value": "Attendance"},
                                "url": {
                                    "value": "/guardians/student-ui/recent-attendance/student-id/1879"
                                },
                                "selected": {"value": False},
                                "id": {"value": 3},
                            },
                            "leaf": True,
                        },
                    ]
                },
                "label": "Student profile",
            },
            "xtype": "mis-subnavcolumn",
        }
    },
    "helpCentreUrl": "https://support.arbor-education.com/",
    "navigation": None,
}

# What that content URL returns: the data the page itself never carried.
ASSIGNMENTS_CONTENT = {
    "type": "component",
    "componentName": "Arbor.table.Assignments",
    "props": {
        "rows": [
            {
                "name": "Photosynthesis worksheet",
                "subject": "Biology",
                "dueDate": "2026-09-25",
                "status": "Not submitted",
                "mark": None,
            },
            {
                "name": "Macbeth Act 2 essay",
                "subject": "English",
                "dueDate": "2026-09-18",
                "status": "Submitted",
                "mark": "B+",
            },
        ]
    },
}

# A KPI panel names its content with `url` rather than `pageUrl`.
KPI_SHELL_PAGE = {
    "type": "page",
    "content": [
        {
            "componentName": "Arbor.container.LayoutColumn",
            "type": "component",
            "content": [
                {
                    "componentName": "Arbor.panel.NewKpi",
                    "xtype": "new-kpi-panel",
                    "props": {
                        "id": "kpi",
                        "title": "Attendance",
                        "url": "/guardians/student-ui/attendance-kpi/student-id/1879",
                        "kpiCount": "4",
                    },
                }
            ],
            "xtype": "mis-layoutcolumn",
            "props": {},
        }
    ],
}


# KPI tiles: the number is `mainValue`, not `value`.
KPI_TILE_CONTENT = [
    {
        "title": "Assignments that are due",
        "description": "This week",
        "mainValue": "3",
        "mainValueColor": "blue",
        "url": "/guardians/student-ui/assignments-due/student-id/1879",
    },
    {
        "title": "Overdue Assignments",
        "description": "Not handed in",
        "mainValue": "1",
        "url": "/guardians/student-ui/assignments-overdue/student-id/1879",
    },
    {
        "title": "Attendance this year",
        "description": "Since September",
        "mainValue": "96.4%",
        "url": "/guardians/student-ui/attendance/student-id/1879",
    },
]

# Behaviour logged as date-labelled property rows holding HTML, which is neither
# a table nor a conventional label/value metric.
BEHAVIOUR_PROPERTY_ROWS = {
    "type": "page",
    "content": [
        {
            "xtype": "mis-section",
            "props": {"title": "Behaviour this year"},
            "content": [
                {
                    "xtype": "mis-subsection",
                    "props": {"title": "Positive"},
                    "content": [
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "fieldLabel": "21 Sep 2026",
                                "value": "<b>Excellent work</b>&nbsp;2 points&nbsp;Biology",
                            },
                        },
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "fieldLabel": "18 Sep 2026",
                                "value": "<b>Late to lesson</b>&nbsp;1 point&nbsp;Negative",
                            },
                        },
                    ],
                }
            ],
        }
    ],
}

# A calendar component names the object whose events it draws.
CALENDAR_COMPONENT_PAGE = {
    "type": "page",
    "content": [
        {
            "xtype": "mis-layoutcolumn",
            "content": [
                {
                    "xtype": "mis-calendar-calendar",
                    "props": {
                        "itemId": "student-calendar",
                        "defaultView": "timeGrid",
                        "referenceObjectTypeId": 43,
                        "referenceObjectId": 1879,
                    },
                }
            ],
        }
    ],
}

# What an action URL answers with: a form, carrying nothing about the child.
LOG_ABSENCE_SLIDEOVER = {
    "type": "slideover",
    "content": [
        {
            "xtype": "mis-section",
            "content": [
                {
                    "xtype": "mis-combobox",
                    "props": {"name": "from_time", "fieldLabel": "Absent from"},
                }
            ],
        }
    ],
}

# A page whose only content URL is an action button.
PAGE_WITH_ACTION_BUTTON_ONLY = {
    "type": "page",
    "content": [
        {
            "xtype": "container",
            "content": [
                {
                    "xtype": "mis-button-load-page",
                    "props": {
                        "text": "Log Absence",
                        "role": "load-page",
                        "pageUrl": "/guardians/attendance-ui/log-absence/student-id/1879",
                    },
                }
            ],
        }
    ],
}


# A dashboard whose per-student navigation covers the routes a real guardian
# portal offers, including the calendar page.
WROTHAM_SHAPED_DASHBOARD = {
    "type": "page",
    "content": [
        {
            "xtype": "container",
            "content": [
                {
                    "text": "Amelia Example",
                    "url": "/guardians/student-profile/index/student-id/40219",
                }
            ],
        }
    ],
    "subNav": {
        "props": {
            "props": {
                "treeData": {
                    "items": [
                        {
                            "fields": {
                                "text": {"value": caption},
                                "url": {"value": url},
                            },
                            "leaf": True,
                        }
                        for caption, url in (
                            ("Attendance", "/guardians/attendance/index/student-id/40219"),
                            ("Behaviour", "/guardians/behaviour/index/student-id/40219"),
                            ("Assignments", "/guardians/assignments/index/student-id/40219"),
                            ("Calendar", "/guardians/calendar/index/student-id/40219"),
                            ("Log Absence", "/guardians/absence/new/student-id/40219"),
                        )
                    ]
                }
            },
            "xtype": "mis-subnavcolumn",
        }
    },
}


# The calendar POST's response, wrapped the way the ExtJS bundle unwraps it:
# items[0].fields.response.value
CALENDAR_POST_RESPONSE = {
    "success": True,
    "items": [
        {
            "fields": {
                "response": {
                    "value": {
                        "currentView": {
                            "view": "period",
                            "start": "2026-09-22",
                            "end": "2026-09-28",
                        },
                        "events": CALENDAR_ENDPOINT["items"],
                    }
                }
            }
        }
    ],
}


# /guardians/student/kpis/id/<id>/ -- the school's headline figures. Each entry
# is a caption plus a rendered fragment holding the number. Captions and values
# copied in shape from a live account.
STUDENT_KPIS = {
    "success": True,
    "items": [
        {
            "fields": {
                "title": {"value": "Attendance (2026/2027)"},
                "html": {
                    "value": "<div class='kpi'><span>100%</span>"
                    "<small>100% of 4 sessions</small></div>"
                },
                "url": {"value": "/guardians/student-ui/recent-attendance/student-id/1879"},
            }
        },
        {
            "fields": {
                "title": {"value": "Positive Behavioural Incidents - this term"},
                "html": {"value": "<div class='kpi'><span>35</span></div>"},
            }
        },
        {
            "fields": {
                "title": {"value": "Negative Behavioural Incidents - this term"},
                "html": {"value": "<div class='kpi'><span>0</span></div>"},
            }
        },
        {
            "fields": {
                "title": {"value": "Neutral Behavioural Incidents - this term"},
                "html": {"value": "<div class='kpi'><span>0</span></div>"},
            }
        },
    ],
}

# /guardians/widget-data/get-calendar-data/student-id/<id>/ -- the timetable, as
# events with real datetimes rather than rendered HTML.
GUARDIAN_CALENDAR = {
    "success": True,
    "items": [
        {
            "fields": {
                "start_datetime": {"value": "2026-09-22T09:00:00"},
                "end_datetime": {"value": "2026-09-22T10:00:00"},
                "title": {"value": "Biology"},
                "location": {"value": "S4"},
                "url": {"value": "/guardians/session-ui/overview/id/95780"},
            }
        },
        {
            "fields": {
                "start_datetime": {"value": "2026-09-22T10:05:00"},
                "end_datetime": {"value": "2026-09-22T11:05:00"},
                "title": {"value": "Mathematics"},
                "location": {"value": "M2"},
            }
        },
    ],
}


# The guardian dashboard as a real school serves it: sectioned property rows.
# This is where work that is actually due is listed, and where the meal balance
# is -- not on the assignments or meals pages.
DASHBOARD_WITH_SECTIONS = {
    "type": "page",
    "content": [
        {
            "xtype": "mis-layoutcolumn",
            "content": [
                {
                    "xtype": "mis-section",
                    "props": {"title": "Notices"},
                    "content": [
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "value": "Alexander Example has no hearing test details",
                                "url": "/guardians/medical-ui/add-hearing-test/student-id/1879",
                            },
                        }
                    ],
                },
                {
                    "xtype": "mis-section",
                    "props": {"title": "Assignments that are due"},
                    "content": [
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "value": "9En4: Term 1 - Task 1 (Due 24 Sep 2026)",
                                "description": "Waiting for student to submit",
                                "url": "/guardians/student-ui/schoolwork-overview"
                                "/schoolwork-id/1708/student-id/1879",
                            },
                        },
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "value": "9Sc3: Cell biology (Due 25 Sep 2026)",
                                "description": "Waiting for student to submit",
                            },
                        },
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "value": "9Ma3: Flash Cards (Due 20 Sep 2026)",
                                "description": "Submitted",
                            },
                        },
                        # A row that is not an assignment at all.
                        {
                            "xtype": "mis-property-row",
                            "props": {"value": "View all assignments"},
                        },
                    ],
                },
                {
                    "xtype": "mis-section",
                    "props": {"title": "Accounts "},
                    "content": [
                        {
                            "xtype": "mis-property-row",
                            "props": {
                                "value": "Alexander Example: Meals",
                                "description": "Balance: £4.15",
                                "url": "/guardians/customer-account-ui/dashboard"
                                "/customer-account-id/5964",
                            },
                        }
                    ],
                },
            ],
        }
    ],
}
