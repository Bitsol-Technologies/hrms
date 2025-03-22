# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


from datetime import datetime

import pytz

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import DATE_FORMAT, cint, get_datetime

from hrms.hr.doctype.shift_assignment.shift_assignment import get_actual_start_end_datetime_of_shift
from hrms.hr.utils import (
	get_distance_between_coordinates,
	set_geolocation_from_coordinates,
	validate_active_employee,
)


class CheckinRadiusExceededError(frappe.ValidationError):
	pass


class EmployeeCheckin(Document):
	def validate(self):
		validate_active_employee(self.employee)
		self.validate_previous_date_logs()
		self.validate_date_time()
		self.validate_duplicate_log()
		self.fetch_shift()
		self.set_geolocation()
		self.validate_distance_from_shift_location()
		self.validate_check_leave_on_same_day()
		if self.log_type == "OUT":
			self.validate_current_day_checkin()
		self.validate_same_consecutive_logs()

	def validate_date_time(self):
		date_format = f"{DATE_FORMAT} %H%M%S"
		current_date_time = datetime.strptime(
			datetime.now(pytz.timezone("Asia/Karachi")).strftime(date_format), date_format
		)
		if get_datetime(self.time) > current_date_time:
			frappe.throw(_("check-{0} can't be set for the future date/time").format(self.log_type.lower()))

	def validate_previous_date_logs(self):
		current_date = datetime.now().date()

		# Check if the attendance date is earlier than the current date
		if frappe.utils.getdate(self.time) < current_date:
			frappe.throw(
				_(
					f"Cannot Check-{self.log_type.capitalize()} for past dates. Please select the current date or consult HR department."
				)
			)

	def validate_same_consecutive_logs(self):
		# Get the current date

		# Fetch the last check-in/check-out entry for the employee
		last_log = frappe.db.get_value(
			"Employee Checkin", {"employee": self.employee}, ["log_type", "time"], order_by="time desc"
		)

		if last_log:
			last_log_type, last_log_time = last_log

			# Get the date part of the last log time
			last_log_date = frappe.utils.getdate(last_log_time)

			# Check if the last log type is the same as the current log type and on the same day
			if self.log_type.lower() == last_log_type.lower() and last_log_date == frappe.utils.getdate(
					frappe.utils.nowdate()
			):
				frappe.throw(
					_(
						"You cannot mark consecutive 'Check-{0}' entries on the same day without a '{1}' entry first."
					).format(
						self.log_type.lower(), "Check-out" if self.log_type.lower() == "in" else "Check-in"
					)
				)
			if frappe.utils.get_datetime(self.time) < frappe.utils.get_datetime(last_log_time):
				frappe.throw(_("Current log time cannot be earlier than the previous log time."))

	def validate_check_leave_on_same_day(self):
		checkin_date = self.time.split(" ")[0]
		doc = frappe.db.exists(
			"Leave Application",
			{
				"employee": self.employee,
				"from_date": [">=", checkin_date],
				"to_date": ["<=", checkin_date],
				"status": "Approved",
				"half_day": ["!=", True],
			},
		)
		if doc:
			frappe.throw(
				_("<b>Not Permitted:</b> Leave has been approved on the same date {0}").format(checkin_date)
			)

	def validate_duplicate_log(self):
		doc = frappe.db.exists(
			"Employee Checkin",
			{
				"employee": self.employee,
				"time": self.time,
				"name": ("!=", self.name),
				"log_type": self.log_type,
			},
		)
		if doc:
			doc_link = frappe.get_desk_link("Employee Checkin", doc)
			frappe.throw(
				_("This employee already has a log with the same timestamp.{0}").format("<Br>" + doc_link)
			)

	@frappe.whitelist()
	def set_geolocation(self):
		set_geolocation_from_coordinates(self)

	def validate_current_day_checkin(self):
		docs = frappe.db.sql(
			"""SELECT COUNT(log_type) FROM `tabEmployee Checkin` WHERE CAST(time as DATE)=%(time_val)s AND
			log_type='IN' AND employee = %(employee)s""",
			{"time_val": self.time.split(" ")[0], "employee": self.employee},
		)
		if docs[0][0] < 1:
			frappe.throw(_("Please add check-in first"))
		else:
			pass

	@frappe.whitelist()
	def fetch_shift(self):
		if not (
				shift_actual_timings := get_actual_start_end_datetime_of_shift(
					self.employee, get_datetime(self.time), True
				)
		):
			self.shift = None
			return

		if (
				shift_actual_timings.shift_type.determine_check_in_and_check_out
				== "Strictly based on Log Type in Employee Checkin"
				and not self.log_type
				and not self.skip_auto_attendance
		):
			frappe.throw(
				_("Log Type is required for check-ins falling in the shift: {0}.").format(
					shift_actual_timings.shift_type.name
				)
			)
		if not self.attendance:
			self.shift = shift_actual_timings.shift_type.name
			self.shift_actual_start = shift_actual_timings.actual_start
			self.shift_actual_end = shift_actual_timings.actual_end
			self.shift_start = shift_actual_timings.start_datetime
			self.shift_end = shift_actual_timings.end_datetime

	def validate_distance_from_shift_location(self):
		if not frappe.db.get_single_value("HR Settings", "allow_geolocation_tracking"):
			return

		if not (self.latitude or self.longitude):
			frappe.throw(_("Latitude and longitude values are required for checking in."))

		assignment_locations = frappe.get_all(
			"Shift Assignment",
			filters={
				"employee": self.employee,
				"shift_type": self.shift,
				"start_date": ["<=", self.time],
				"shift_location": ["is", "set"],
				"docstatus": 1,
			},
			or_filters=[["end_date", ">=", self.time], ["end_date", "is", "not set"]],
			pluck="shift_location",
		)
		if not assignment_locations:
			return

		checkin_radius, latitude, longitude = frappe.db.get_value(
			"Shift Location", assignment_locations[0], ["checkin_radius", "latitude", "longitude"]
		)
		if checkin_radius <= 0:
			return

		distance = get_distance_between_coordinates(latitude, longitude, self.latitude, self.longitude)
		if distance > checkin_radius:
			frappe.throw(
				_("You must be within {0} meters of your shift location to check in.").format(checkin_radius),
				exc=CheckinRadiusExceededError,
			)


@frappe.whitelist()
def add_log_based_on_employee_field(
		employee_field_value,
		timestamp,
		device_id=None,
		log_type=None,
		skip_auto_attendance=0,
		employee_fieldname="attendance_device_id",
):
	"""Finds the relevant Employee using the employee field value and creates a Employee Checkin.

	:param employee_field_value: The value to look for in employee field.
	:param timestamp: The timestamp of the Log. Currently expected in the following format as string: '2019-05-08 10:48:08.000000'
	:param device_id: (optional)Location / Device ID. A short string is expected.
	:param log_type: (optional)Direction of the Punch if available (IN/OUT).
	:param skip_auto_attendance: (optional)Skip auto attendance field will be set for this log(0/1).
	:param employee_fieldname: (Default: attendance_device_id)Name of the field in Employee DocType based on which employee lookup will happen.
	"""

	if not employee_field_value or not timestamp:
		frappe.throw(_("'employee_field_value' and 'timestamp' are required."))

	employee = frappe.db.get_values(
		"Employee",
		{employee_fieldname: employee_field_value},
		["name", "employee_name", employee_fieldname],
		as_dict=True,
	)
	if employee:
		employee = employee[0]
	else:
		frappe.throw(
			_("No Employee found for the given employee field value. '{}': {}").format(
				employee_fieldname, employee_field_value
			)
		)

	doc = frappe.new_doc("Employee Checkin")
	doc.employee = employee.name
	doc.employee_name = employee.employee_name
	doc.time = timestamp
	doc.device_id = device_id
	doc.log_type = log_type
	if cint(skip_auto_attendance) == 1:
		doc.skip_auto_attendance = "1"
	doc.insert()

	return doc


@frappe.whitelist()
def bulk_fetch_shift(checkins: list[str] | str) -> None:
	if isinstance(checkins, str):
		checkins = frappe.json.loads(checkins)
	for d in checkins:
		doc = frappe.get_doc("Employee Checkin", d)
		doc.fetch_shift()
		doc.flags.ignore_validate = True
		doc.save()


def mark_attendance_and_link_log(
		logs,
		attendance_status,
		attendance_date,
		working_hours=None,
		late_entry=False,
		early_exit=False,
		in_time=None,
		out_time=None,
		shift=None,
):
	frappe.utils.logger.set_log_level("DEBUG")
	logger = frappe.logger("checkin", allow_site=True, file_count=10)

	"""Creates an attendance and links the attendance to the Employee Checkin.
	Note: If attendance is already present for the given date, the logs are marked as skipped and no exception is thrown.

	:param logs: The List of 'Employee Checkin'.
	:param attendance_status: Attendance status to be marked. One of: (Present, Absent, Half Day, Skip). Note: 'On Leave' is not supported by this function.
	:param attendance_date: Date of the attendance to be created.
	:param working_hours: (optional)Number of working hours for the given date.
	"""
	log_names = [x.name for x in logs]
	employee = logs[0].employee

	if attendance_status == "Skip":
		skip_attendance_in_checkins(log_names)
		logger.info("Attendance skipped due to attendance_status")
		return None

	elif attendance_status in ("Present", "Absent", "Half Day"):
		try:
			frappe.db.savepoint("attendance_creation")
			attendance = frappe.new_doc("Attendance")
			attendance.update(
				{
					"doctype": "Attendance",
					"employee": employee,
					"attendance_date": attendance_date,
					"status": attendance_status,
					"working_hours": working_hours,
					"shift": shift,
					"late_entry": late_entry,
					"early_exit": early_exit,
					"in_time": in_time,
					"out_time": out_time,
				}
			).submit()

			if attendance_status == "Absent":
				attendance.add_comment(
					text=_("Employee was marked Absent for not meeting the working hours threshold.")
				)
			message = f"{attendance_status} marked for {employee} in shift {shift}. Time for checkin is {in_time} and checkout is {out_time} and total working hours are {working_hours}"
			logger.info(message)
			logger.info(f"date: {attendance_date}, late_entry: {late_entry}, early_exit: {early_exit}")
			update_attendance_in_checkins(log_names, attendance.name)
			return attendance

		except frappe.ValidationError as e:
			handle_attendance_exception(log_names, e)

	else:
		frappe.throw(_("{} is an invalid Attendance Status.").format(attendance_status))


def calculate_working_hours(logs, check_in_out_type, working_hours_calc_type):
	"""Given a set of logs in chronological order calculates the total working hours based on the parameters.
	Zero is returned for all invalid cases.

	:param logs: The List of 'Employee Checkin'.
	:param check_in_out_type: One of: 'Alternating entries as IN and OUT during the same shift', 'Strictly based on Log Type in Employee Checkin'
	:param working_hours_calc_type: One of: 'First Check-in and Last Check-out', 'Every Valid Check-in and Check-out'
	"""
	total_hours = 0
	in_time = out_time = None
	if check_in_out_type == "Alternating entries as IN and OUT during the same shift":
		in_time = logs[0].time
		if len(logs) >= 2:
			out_time = logs[-1].time
		if working_hours_calc_type == "First Check-in and Last Check-out":
			# assumption in this case: First log always taken as IN, Last log always taken as OUT
			total_hours = time_diff_in_hours(in_time, logs[-1].time)
		elif working_hours_calc_type == "Every Valid Check-in and Check-out":
			logs = logs[:]
			while len(logs) >= 2:
				total_hours += time_diff_in_hours(logs[0].time, logs[1].time)
				del logs[:2]

	elif check_in_out_type == "Strictly based on Log Type in Employee Checkin":
		if working_hours_calc_type == "First Check-in and Last Check-out":
			first_in_log_index = find_index_in_dict(logs, "log_type", "IN")
			first_in_log = logs[first_in_log_index] if first_in_log_index or first_in_log_index == 0 else None
			last_out_log_index = find_index_in_dict(reversed(logs), "log_type", "OUT")
			last_out_log = (
				logs[len(logs) - 1 - last_out_log_index]
				if last_out_log_index or last_out_log_index == 0
				else None
			)
			in_time = getattr(first_in_log, "time", None)
			out_time = getattr(last_out_log, "time", None)
			if first_in_log and last_out_log:
				total_hours = time_diff_in_hours(in_time, out_time)
		elif working_hours_calc_type == "Every Valid Check-in and Check-out":
			in_log = out_log = None
			for log in logs:
				if in_log and out_log:
					if not in_time:
						in_time = in_log.time
					out_time = out_log.time
					total_hours += time_diff_in_hours(in_log.time, out_log.time)
					in_log = out_log = None
				if not in_log:
					in_log = log if log.log_type == "IN" else None
					if in_log and not in_time:
						in_time = in_log.time
				elif not out_log:
					out_log = log if log.log_type == "OUT" else None
			if in_log and not out_log:
				out_time = in_log.shift_end
			if in_log and out_log:
				out_time = out_log.time
			print("in and out:", in_log.time if in_log else None, out_time)
			if in_log and out_time:
				total_hours += time_diff_in_hours(in_log.time, out_time)

	return total_hours, in_time, out_time


def time_diff_in_hours(start, end):
	return round(float((end - start).total_seconds()) / 3600, 2)


def find_index_in_dict(dict_list, key, value):
	return next((index for (index, d) in enumerate(dict_list) if d[key] == value), None)


def handle_attendance_exception(log_names: list, error_message: str):
	frappe.db.rollback(save_point="attendance_creation")
	frappe.clear_messages()
	skip_attendance_in_checkins(log_names)
	add_comment_in_checkins(log_names, error_message)


def add_comment_in_checkins(log_names: list, error_message: str):
	text = "{prefix}<br>{error_message}".format(
		prefix=frappe.bold(_("Reason for skipping auto attendance:")), error_message=error_message
	)

	for name in log_names:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Employee Checkin",
				"reference_name": name,
				"content": text,
			}
		).insert(ignore_permissions=True)


def skip_attendance_in_checkins(log_names: list):
	EmployeeCheckin = frappe.qb.DocType("Employee Checkin")
	(
		frappe.qb.update(EmployeeCheckin)
		.set("skip_auto_attendance", 1)
		.where(EmployeeCheckin.name.isin(log_names))
	).run()


def update_attendance_in_checkins(log_names: list, attendance_id: str):
	EmployeeCheckin = frappe.qb.DocType("Employee Checkin")
	(
		frappe.qb.update(EmployeeCheckin)
		.set("attendance", attendance_id)
		.where(EmployeeCheckin.name.isin(log_names))
	).run()


import frappe
import re
import requests
import json
from datetime import datetime, timedelta
from frappe.utils import today, now, get_datetime


def get_today_date_range():
	today_str = today()  # e.g., "2025-03-18"
	start_dt_str = f"{today_str} 00:00:00"
	end_dt_str = f"{today_str} 23:59:59"
	return today_str, start_dt_str, end_dt_str, get_datetime(start_dt_str), get_datetime(end_dt_str)


def get_employee_checkins(log_type):
	today_str, start_dt_str, end_dt_str, _, _ = get_today_date_range()
	return frappe.get_all(
		"Employee Checkin",
		filters={"time": ["between", [start_dt_str, end_dt_str]], "log_type": log_type},
		fields=["name", "employee", "time"]
	)


def get_default_workspace_id(custom_api_key):
	"""
	Fetches all workspaces accessible by the API key and returns the first workspace's ID.
	"""
	headers = {"X-Api-Key": custom_api_key}
	url = "https://api.clockify.me/api/v1/workspaces"
	try:
		response = requests.get(url, headers=headers)
		response.raise_for_status()
		workspaces = response.json()
		if workspaces:
			return [ws.get("id") for ws in workspaces if ws.get("id")]
	except Exception as e:
		frappe.log_error(f"Error fetching workspaces: {e}", "Clockify Task")
	return []


def get_clockify_user_id_by_email(custom_api_key, workspace_id, email):
	"""
	Looks up the Clockify user ID for a given email within a workspace.
	"""
	headers = {"X-Api-Key": custom_api_key}
	url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/users"
	try:
		response = requests.get(url, headers=headers)
		response.raise_for_status()
		users = response.json()
		for user in users:
			if user.get("email") == email:
				return user.get("id")
	except Exception as e:
		frappe.log_error(f"Error fetching Clockify users for workspace {workspace_id}: {e}", "Clockify Task")
	return None


def get_employee_clockify_details(employee_id):
	"""
    Retrieves Clockify details for an employee. If workspace or user ID is missing,
    it uses the API key to fetch a default workspace and then looks up the user by email.
    """
	emp = frappe.get_doc("Employee", employee_id)
	user_id = emp.get("user_id")
	custom_api_key = emp.get("custom_clockify_api_key")
	custom_user_id = emp.get("custom_clockify_user_id")  # Might be empty
	workspace_raw = emp.get("custom_clockify_workspaces")  # Might be empty

	# If workspace is not provided, fetch a default workspace using the API key.
	workspace_ids = [ws.strip() for ws in (workspace_raw or "").split(",") if ws.strip()]
	if not workspace_ids and custom_api_key:
		workspace_ids = get_default_workspace_id(custom_api_key)

	# If user ID is not provided, try to look it up using employee's email.
	if not custom_user_id:
		email = emp.get("user") or emp.get("user_id")
		if custom_api_key and workspace_ids and email:
			custom_user_id = get_clockify_user_id_by_email(custom_api_key, workspace_ids[0], email)

	return (custom_api_key, custom_user_id, workspace_ids, emp, user_id)


def is_clockify_timer_active(custom_api_key, workspace_id, clockify_user_id):
	"""
	Check Clockify for an active timer for the given user.
	Returns True if an active (in-progress) timer is found, else False.
	"""
	headers = {"X-Api-Key": custom_api_key}
	active_url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/user/{clockify_user_id}/time-entries?in-progress=true"

	try:
		response = requests.get(active_url, headers=headers)
		response.raise_for_status()
		active_entries = response.json()
		return bool(active_entries)
	except Exception as e:
		frappe.log_error(f"Clockify API error for user {clockify_user_id}: {e}", "Clockify Task")
		return False


def get_clockify_time_entries(custom_api_key, workspace_id, clockify_user_id, start_dt, end_dt):
	"""
	Fetch all Clockify time entries for the given user between start_dt and end_dt.
	Returns a list of time entry objects.
	"""
	headers = {"X-Api-Key": custom_api_key}
	# Format in ISO 8601 with a trailing "Z" to indicate UTC timezone
	start_str = start_dt.isoformat() + "Z"
	end_str = end_dt.isoformat() + "Z"
	entries_url = f"https://api.clockify.me/api/v1/workspaces/{workspace_id}/user/{clockify_user_id}/time-entries?start={start_str}&end={end_str}"

	try:
		response = requests.get(entries_url, headers=headers)
		response.raise_for_status()
		entries = response.json()
		return entries
	except Exception as e:
		frappe.log_error(f"Error fetching Clockify entries for user {clockify_user_id}: {e}", "Clockify Task")
		return []


def get_slack_user_id(email):
	"""
	Fetch Slack User ID using the given email address.
	"""
	system_settings = frappe.get_single("System Settings")
	slack_api_url = "https://slack.com/api/users.lookupByEmail"
	slack_token = system_settings.slack_token

	headers = {
		"Authorization": f"Bearer {slack_token}",
		"Content-Type": "application/json"
	}
	response = requests.get(slack_api_url, headers=headers, params={"email": email})
	data = response.json()
	if data.get("ok"):
		return data["user"]["id"]
	else:
		frappe.log_error(f"Error fetching Slack user ID for {email}: {data.get('error')}", "Slack Notification")
		return None


def send_slack_message_for_employee(emails, message):
	"""
	Loop over a list of emails, fetch each user's Slack ID, and send them a Slack message.
	"""
	system_settings = frappe.get_single("System Settings")
	slack_post_message_url = "https://slack.com/api/chat.postMessage"
	slack_token = system_settings.slack_token
	env = frappe.db.get_single_value("FCM Notification Settings", "environment")

	for email in emails:
		if "@" not in email:
			user_id = email
		else:
			user_id = get_slack_user_id(email)
			if not user_id:
				frappe.log_error(f"Could not fetch Slack user ID for {email}", "Slack Notification")
				continue

		payload = {
			"channel": user_id,
			"text": f"[{env}] {message}" if env else message
		}
		headers = {
			"Authorization": f"Bearer {slack_token}",
			"Content-Type": "application/json"
		}
		response = requests.post(slack_post_message_url, headers=headers, data=json.dumps(payload))
		result = response.json()
		if not result.get("ok"):
			frappe.log_error(f"Error sending Slack message to {email}: {result.get('error')}", "Slack Notification")


def check_today_checkins():
	"""
	Scheduled task that:
	  - Retrieves all Employee Checkin records for today.
	  - For each checkin that is at least 4 hours old:
		  • Checks if there is an active Clockify timer.
		  • Checks if any time entries have been logged since the checkin.
	  - If both are false, sends a Slack reminder notification.
	"""

	checkins = get_employee_checkins("IN")

	for checkin in checkins:
		checkin_dt = get_datetime(checkin["time"])
		current_dt = get_datetime(now())
		time_since_checkin = current_dt - checkin_dt

		if time_since_checkin < timedelta(hours=4):
			# Skip checkins that are less than 4 hours old
			continue

		# Check if the employee has checked out (log_type "OUT") after this checkin.
		checkout_records = frappe.get_all(
			"Employee Checkin",
			filters={
				"employee": checkin["employee"],
				"log_type": "OUT",
				"time": [">", checkin["time"]]
			},
			fields=["name"]
		)
		if checkout_records:
			# print(f"Employee {checkin['employee']} has checked out after checkin {checkin['name']}; skipping reminder.")
			continue

		custom_api_key, custom_user_id, workspace_ids, emp, user_id = get_employee_clockify_details(checkin.employee)

		if not (custom_api_key and custom_user_id and workspace_ids):
			# msg = f"Employee {emp.name} missing one or more custom Clockify credentials."
			frappe.log_error(msg, "Clockify Check")
			continue

		# Check for active timer in any workspace
		active_timer = False
		for ws in workspace_ids:
			if is_clockify_timer_active(custom_api_key, ws, custom_user_id):
				active_timer = True
				break

		if active_timer:
			# print(f"Employee {emp.name} has an active Clockify timer in at least one workspace; skipping reminder.")
			continue

		# Check for time entries in any workspace
		entries_found = False
		for ws in workspace_ids:
			entries = get_clockify_time_entries(custom_api_key, ws, custom_user_id, checkin_dt, current_dt)
			if entries:
				entries_found = True
				break

		if entries_found:
			# print(f"Employee {emp.name} has logged time entries in at least one workspace; skipping reminder.")
			continue

		# If no active timer and no time entries across all workspaces, send Slack reminder
		reminder_message = (
			"Reminder: You are checked in, but no time is logged in Clockify for the past 4 hours. "
			"Please start your Clockify timer to ensure compliance."
		)
		email = emp.get("user") or emp.get("user_id")
		if not email:
			msg = f"Employee {emp.name} missing email for Slack notification."
			frappe.log_error(msg, "Clockify Check")
			continue

		send_slack_message_for_employee([email], reminder_message)

		from fcm_notification.send_notification import send_push_to_user
		push_title = "Clockify Timer Reminder"
		send_push_to_user(email, push_title, reminder_message)


def build_compliance_report_table(non_compliant):
	# Define fixed widths for each column (adjust as needed)
	header = f"{'Employee':20} | {'Checkin':10} | {'Checkout':10} | {'Reason':30}\n"
	header += "-" * 80 + "\n"
	rows = []
	for rec in non_compliant:
		emp = rec["employee"]
		checkin = rec["checkin"].strftime('%I:%M %p') if rec["checkin"] else "N/A"
		checkout = rec["checkout"].strftime('%I:%M %p') if rec["checkout"] else "N/A"
		reason = rec["reason"]
		row = f"{emp:20} | {checkin:10} | {checkout:10} | {reason:30}"
		rows.append(row)
	table_text = header + "\n".join(rows)
	return f"```{table_text}```"


def get_all_active_employees():
	"""
	Fetch all active employees from the Employee Doctype.
	"""
	return frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name"]
	)


def send_compliance_report(non_compliant, today_str):
	if non_compliant:
		# Build a plain text header
		header_text = f"📢  Daily Clockify Compliance Report – {today_str}\n"
		header_text += f"Total Non-Compliant Employees: {len(non_compliant)}\n\n"
		# Build the table as a code block 
		report_message = header_text + build_compliance_report_table(non_compliant)
	else:
		# Build a plain text message
		report_message = f"📢 Daily Clockify Compliance Report – {today_str}\nAll employees are compliant with Clockify logs for today."

	target = "C08JA26QG84"  # Management Channel
	send_slack_message_for_employee([target], report_message)


def parse_iso8601_duration(duration_str):
	"""
	Parse an ISO 8601 duration string (e.g., "PT2H16M30S") and return total seconds.
	"""
	pattern = re.compile(r'^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$')
	match = pattern.match(duration_str)
	if not match:
		return 0
	hours = int(match.group("hours") or 0)
	minutes = int(match.group("minutes") or 0)
	seconds = int(match.group("seconds") or 0)
	return hours * 3600 + minutes * 60 + seconds


def sum_clockify_durations(entries):
	"""
	Given a list of Clockify time entries, compute the total duration in seconds.
	Assumes the "duration" field is always provided as an ISO 8601 duration string.
	If a "duration" field is not present, falls back to calculating the duration from the timeInterval.
	"""
	total_seconds = 0
	for entry in entries:
		if entry.get("duration"):
			total_seconds += parse_iso8601_duration(entry["duration"])
		elif entry.get("timeInterval"):
			interval = entry["timeInterval"]
			start_str = interval.get("start")
			end_str = interval.get("end")
			if start_str and end_str:
				start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
				end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
				total_seconds += (end_dt - start_dt).total_seconds()
	return total_seconds


def award_energy_points(user, points, reason, reference_doctype=None, reference_name=None, rule=None):
	energy_point = frappe.get_doc({
		"doctype": "Energy Point Log",
		"user": user,  # The recipient of the points
		"points": points,  # Number of points to award
		"reason": reason,  # Reason for awarding points
		"rule": rule,  # Optional: Define a rule if needed
		"reference_doctype": reference_doctype,  # Optional: Related document type
		"reference_name": reference_name,  # Optional: Related document name
		"type": "Auto"  # Can be 'Auto' or 'Appreciation'
	})

	energy_point.insert(ignore_permissions=True)
	frappe.db.commit()
	return f"Energy points awarded to {user}!"


def update_employee_times(records, key, employee_times):
	"""Updates the employee_times dictionary with check-in and check-out times."""
	for rec in records:
		emp_id = rec.employee
		dt = get_datetime(rec.time)
		if emp_id not in employee_times:
			employee_times[emp_id] = {}
		if key == "checkin":
			if "checkin" not in employee_times[emp_id] or dt < employee_times[emp_id]["checkin"]:
				employee_times[emp_id]["checkin"] = dt
		elif key == "checkout":
			if "checkout" not in employee_times[emp_id] or dt > employee_times[emp_id]["checkout"]:
				employee_times[emp_id]["checkout"] = dt


def send_daily_compliance_report():
	"""
	End-of-Day Compliance Report (to be run at 11 PM):
	- Checks employee check-ins and Clockify logs to determine compliance.
	- Sends a compliance report to Slack.
	"""
	today_str, _, _, start_dt, end_dt = get_today_date_range()
	in_checkins = get_employee_checkins("IN")
	out_checkins = get_employee_checkins("OUT")

	employee_times = {}
	update_employee_times(in_checkins, "checkin", employee_times)
	update_employee_times(out_checkins, "checkout", employee_times)

	MIN_REQUIRED_SECONDS = 4 * 3600  # 4 hours in seconds
	non_compliant = []

	for emp_id, times in employee_times.items():
		if "checkin" not in times or "checkout" not in times:
			continue

		custom_api_key, custom_user_id, workspace_ids, emp, user_id = get_employee_clockify_details(emp_id)
		if not (custom_api_key and custom_user_id and workspace_ids):
			continue

		# Check if an active timer is running
		if any(is_clockify_timer_active(custom_api_key, ws, custom_user_id) for ws in workspace_ids):
			continue  # Assume compliance if an active timer is running.

		# Fetch Clockify time entries
		total_logged_seconds = sum(
			sum_clockify_durations(get_clockify_time_entries(custom_api_key, ws, custom_user_id, start_dt, end_dt))
			for ws in workspace_ids
		)

		# Determine non-compliance reason
		reason = None
		if total_logged_seconds == 0:
			reason = "No Clockify logs recorded"
		elif total_logged_seconds < MIN_REQUIRED_SECONDS:
			hours, minutes = divmod(total_logged_seconds // 60, 60)
			reason = f"Only {hours} hr {minutes} mins logged (< 4 hours)"

		if reason:
			non_compliant.append({
				"employee": emp.get("employee_name", emp.name),
				"checkin": times["checkin"],
				"checkout": times["checkout"],
				"reason": reason
			})
			award_energy_points(user_id, -1, reason, "Employee", emp_id)
		else:
			award_energy_points(user_id, 1, "Compliant with work logs", "Employee", emp_id)

	# Scenario B: Active employees with no check-in record
	active_emps = get_all_active_employees()
	for emp_dict in active_emps:
		emp_id = emp_dict["name"]
		if emp_id in employee_times:
			# if checkin done, skip
			continue
		# else no checkin: check for clockify entries or active timer
		custom_api_key, custom_user_id, workspace_ids, emp, user_id = get_employee_clockify_details(emp_id)
		if not (custom_api_key and custom_user_id and workspace_ids):
			print("no id found for ", user_id)
			continue

		# Check if there are any Clockify logs or active timer
		if any(get_clockify_time_entries(custom_api_key, ws, custom_user_id, start_dt, end_dt) for ws in workspace_ids) \
				or any(is_clockify_timer_active(custom_api_key, ws, custom_user_id) for ws in workspace_ids):
			#  clockify logs are present but no checkin
			non_compliant.append({
				"employee": emp.get("employee_name", emp.name),
				"checkin": "",
				"checkout": "",
				"reason": "Clockify logs present but no check-in recorded"
			})
			award_energy_points(user_id, -1, "Clockify logs present but no check-in recorded", "Employee", emp_id)
		else:
			# no clockify , no checkin but employee is active 
			# Check if the employee is on leave
			if is_employee_on_leave(emp_id, today_str):  # if on leave, compliance
				award_energy_points(user_id, 0, "On leave", "Employee", emp_id)
			else:
				non_compliant.append({
					"employee": emp.get("employee_name", emp.name),
					"checkin": "",
					"checkout": "",
					"reason": "No check-in, No Clockify logs, No leave recorded"
				})
				award_energy_points(user_id, -1, "Absent (No check-in, No Clockify logs)", "Employee", emp_id)

	send_compliance_report(non_compliant, today_str)


def is_employee_on_leave(emp_id, date):
	"""
	Checks the Attendance Doctype to see if the employee is marked as 'On Leave' or 'Half Day'.

	:param emp_id: Employee ID
	:param date: The date to check leave status (YYYY-MM-DD)
	:return: True if the employee is on leave, False otherwise.
	"""
	attendance = frappe.get_value("Attendance", {"employee": emp_id, "attendance_date": date}, "status")
	return attendance in ["On Leave", "Half Day"]
