# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
import urllib.parse
import json

def execute(filters=None):
	filters = filters or {}
	filters.setdefault("start_date", None)
	filters.setdefault("end_date", None)
	filters.setdefault("employee", None)

	# Normalize empty strings to None
	for key in ("start_date", "end_date", "employee"):
		if not filters[key]:
			filters[key] = None

	data = frappe.db.sql("""
		SELECT
			emp.employee_name AS employee_name,
			usr.name AS user_id,
			SUM(CASE WHEN hr_note.type = 'Daily Check-in Reminder' THEN 1 ELSE 0 END) AS daily_checkin,
			SUM(CASE WHEN hr_note.type = 'Clockify Time Log Reminder' THEN 1 ELSE 0 END) AS clockify_log,
			SUM(CASE WHEN hr_note.type = 'Goals Meeting Reminder' THEN 1 ELSE 0 END) AS goals_meeting,
			SUM(CASE WHEN hr_note.type = 'Apply For Leave' THEN 1 ELSE 0 END) AS apply_leave,
			SUM(CASE WHEN hr_note.type = 'Apply For WFH' THEN 1 ELSE 0 END) AS apply_wfh,
			SUM(CASE WHEN hr_note.type = 'Monthly Assessment Form Filling' THEN 1 ELSE 0 END) AS assessment_form,
			SUM(CASE WHEN hr_note.type = 'Team lead Feedback Form Filling' THEN 1 ELSE 0 END) AS feedback_form,
			SUM(CASE WHEN hr_note.type = 'Mid-day Status' THEN 1 ELSE 0 END) AS midday_status,
			SUM(CASE WHEN hr_note.type = 'Day End Status' THEN 1 ELSE 0 END) AS day_end,
			SUM(CASE WHEN hr_note.type = 'Day Start Plan' THEN 1 ELSE 0 END) AS day_start,
			SUM(CASE WHEN hr_note.type = 'Post Breaks in Channel' THEN 1 ELSE 0 END) AS post_breaks,
			SUM(CASE WHEN hr_note.type = 'Inactivity Reminder' THEN 1 ELSE 0 END) AS inactivity,
			SUM(CASE WHEN hr_note.type = 'Task Reminder' THEN 1 ELSE 0 END) AS task_reminder,
			SUM(CASE WHEN hr_note.type = 'Other' THEN 1 ELSE 0 END) AS other
		FROM
			`tabHR Notifications` AS hr_note
		JOIN
			`tabNotification User` AS hr_note_user ON hr_note.name = hr_note_user.parent
		JOIN
			`tabUser` AS usr ON hr_note_user.user = usr.name
		JOIN
			`tabEmployee` AS emp ON usr.name = emp.user_id
		WHERE
			(%(start_date)s IS NULL OR hr_note.creation >= %(start_date)s)
			AND (%(end_date)s IS NULL OR hr_note.creation <= %(end_date)s)
			AND (%(employee)s IS NULL OR emp.name = %(employee)s)
		GROUP BY
			emp.name, emp.employee_name, usr.name
		ORDER BY
			emp.employee_name
	""", filters, as_dict=True)

	base_url = "/app/hr-notifications"

	for row in data:
		def build_link(count, notif_type, user_id):
			if not count:
				return "0"
			url_params = {
				"type": notif_type,
				"Notification User.user": user_id,
			}
			start = filters.get("start_date")
			end = filters.get("end_date")

			if start and end:
				url_params["creation"] = json.dumps(["between", [str(start), str(end)]])
			elif start:
				url_params["creation"] = json.dumps([">=", str(start)])
			elif end:
				url_params["creation"] = json.dumps(["<=", str(end)])

			full_url = f"{base_url}?{urllib.parse.urlencode(url_params, doseq=True)}"
			return f'<a href="{full_url}" target="_blank">{count}</a>'

		for key, notif_type in [
			("daily_checkin", "Daily Check-in Reminder"),
			("clockify_log", "Clockify Time Log Reminder"),
			("goals_meeting", "Goals Meeting Reminder"),
			("apply_leave", "Apply For Leave"),
			("apply_wfh", "Apply For WFH"),
			("assessment_form", "Monthly Assessment Form Filling"),
			("feedback_form", "Team lead Feedback Form Filling"),
			("midday_status", "Mid-day Status"),
			("day_end", "Day End Status"),
			("day_start", "Day Start Plan"),
			("post_breaks", "Post Breaks in Channel"),
			("inactivity", "Inactivity Reminder"),
			("task_reminder", "Task Reminder"),
			("other", "Other"),
		]:
			row[key] = build_link(row[key], notif_type, row["user_id"])

	columns = [
		{"label": "Employee Name", "fieldname": "employee_name", "fieldtype": "Data", "width": 200},
		{"label": "Daily Check-in Reminder", "fieldname": "daily_checkin", "fieldtype": "Data", "width": 200},
		{"label": "Clockify Time Log Reminder", "fieldname": "clockify_log", "fieldtype": "Data", "width": 200},
		{"label": "Goals Meeting Reminder", "fieldname": "goals_meeting", "fieldtype": "Data", "width": 200},
		{"label": "Apply For Leave", "fieldname": "apply_leave", "fieldtype": "Data", "width": 200},
		{"label": "Apply For WFH", "fieldname": "apply_wfh", "fieldtype": "Data", "width": 200},
		{"label": "Monthly Assessment Form Filling", "fieldname": "assessment_form", "fieldtype": "Data", "width": 200},
		{"label": "Team lead Feedback form Filling", "fieldname": "feedback_form", "fieldtype": "Data", "width": 200},
		{"label": "Mid-day Status", "fieldname": "midday_status", "fieldtype": "Data", "width": 200},
		{"label": "Day End Status", "fieldname": "day_end", "fieldtype": "Data", "width": 200},
		{"label": "Day Start Plan", "fieldname": "day_start", "fieldtype": "Data", "width": 200},
		{"label": "Post Breaks in Channel", "fieldname": "post_breaks", "fieldtype": "Data", "width": 200},
		{"label": "Inactivity Reminder", "fieldname": "inactivity", "fieldtype": "Data", "width": 200},
		{"label": "Task Reminder", "fieldname": "task_reminder", "fieldtype": "Data", "width": 200},
		{"label": "Other", "fieldname": "other", "fieldtype": "Data", "width": 200},
	]

	return columns, data
