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
