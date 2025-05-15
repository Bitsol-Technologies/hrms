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
	def before_validate(self):
		self.time = get_datetime(self.time).replace(microsecond=0)

	def validate(self):
		validate_active_employee(self.employee)
		self.validate_previous_date_logs()
		self.validate_date_time()
		self.validate_duplicate_log()
		self.validate_time_change()
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
		checkin_date = self.time.date()
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

	def validate_time_change(self):
		if self.attendance and self.has_value_changed("time"):
			frappe.throw(
				title=_("Cannot Modify Time"),
				msg=_(
					"An attendance record is linked to this checkin. Please cancel the attendance before modifying time."
				),
			)

	@frappe.whitelist()
	def set_geolocation(self):
		set_geolocation_from_coordinates(self)

	def validate_current_day_checkin(self):
		docs = frappe.db.sql(
			"""SELECT COUNT(log_type) FROM `tabEmployee Checkin` WHERE CAST(time as DATE)=%(time_val)s AND
			log_type='IN' AND employee = %(employee)s""",
			{"time_val": self.time.date(), "employee": self.employee},
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
			self.offshift = 1
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
			self.offshift = 0
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
				"status": "Active",
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
	latitude=None,
	longitude=None,
):
	"""Finds the relevant Employee using the employee field value and creates a Employee Checkin.

	:param employee_field_value: The value to look for in employee field.
	:param timestamp: The timestamp of the Log. Currently expected in the following format as string: '2019-05-08 10:48:08.000000'
	:param device_id: (optional)Location / Device ID. A short string is expected.
	:param log_type: (optional)Direction of the Punch if available (IN/OUT).
	:param skip_auto_attendance: (optional)Skip auto attendance field will be set for this log(0/1).
	:param employee_fieldname: (Default: attendance_device_id)Name of the field in Employee DocType based on which employee lookup will happen.
	:latitude: (optional) Latitude of the shift location.
	:longitude: (optional) Longitude of the shift location.
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
	doc.latitude = latitude
	doc.longitude = longitude
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
			if attendance_status == "Half Day" and (
				attendance := get_existing_half_day_attendance(employee, attendance_date)
			):
				frappe.db.set_value(
					"Attendance",
					attendance.name,
					{
						"half_day_status": "Present",
						"working_hours": working_hours,
						"shift": shift,
						"late_entry": late_entry,
						"early_exit": early_exit,
						"in_time": in_time,
						"out_time": out_time,
					},
				)
			else:
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
						"half_day_status": "Absent" if attendance_status == "Half Day" else None,
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


def get_existing_half_day_attendance(employee, attendance_date):
	attendance_name = frappe.db.exists(
		"Attendance",
		{
			"employee": employee,
			"attendance_date": attendance_date,
			"status": "Half Day",
			"half_day_status": "Absent",
		},
	)

	if attendance_name:
		attendance_doc = frappe.get_doc("Attendance", attendance_name)
		return attendance_doc
	return None


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


def get_system_clockify_settings():
	settings = frappe.get_single("System Settings")
	api_key = settings.get("clockify_api_key")
	workspaces = [ws.strip() for ws in (settings.get("clockify_workspace_id") or "").split(",") if ws.strip()]
	return api_key, workspaces


def get_employee_checkins(log_type):
	_, start_dt_str, end_dt_str, _, _ = get_today_date_range()
	checkins = frappe.get_all(
		"Employee Checkin",
		filters={"time": ["between", [start_dt_str, end_dt_str]], "log_type": log_type},
		fields=["name", "employee", "time"],
		order_by="time DESC",
	)
	# Keep only the latest check-in per employee
	latest_checkins = {}
	for checkin in checkins:
		if checkin["employee"] not in latest_checkins:
			latest_checkins[checkin["employee"]] = checkin

	return list(latest_checkins.values())  # Return only the latest check-ins


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
		frappe.log_error(f"Error fetching workspaces for api key: {custom_api_key}", "Clockify Task")
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
		frappe.log_error(f"Error fetching Clockify user for email {email} for workspace {workspace_id}", "Clockify Task")
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
		title = "Clockify Task"
		message = f"Error for user {clockify_user_id} checking for active timer"
		frappe.log_error(message, title)
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
		frappe.log_error(f"Error fetching Clockify entries for user {clockify_user_id}", "Clockify Task")
		return []


def get_slack_user_id(email):
	"""
	Fetch Slack User ID from the Employee record using the email address.
	Matches 'user_id' directly (case-insensitive in MariaDB).
	"""
	custom_slack_user_id = frappe.db.get_value(
		"Employee",
		filters={"user_id": email},
		fieldname="custom_slack_user_id"
	)

	if custom_slack_user_id: 
		return custom_slack_user_id
	else:
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
			frappe.log_error(f"Error sending Slack message to {email}", "Slack Notification")


# send reminder to turn on clockify timer
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

		custom_api_key, custom_user_id, workspace_ids, emp, _ = get_employee_clockify_details(checkin.employee)

		if not (custom_api_key and custom_user_id and workspace_ids):
			msg = f"Employee {emp.name} missing one or more custom Clockify credentials."
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
	header = f"{'Employee':20} | {'Checkin':10} | {'Reason':30}\n"
	header += "-" * 80 + "\n"
	rows = []
	for rec in non_compliant:
		emp = rec["employee"]
		checkin = rec["checkin"]
		reason = rec["reason"]
		row = f"{emp:20} | {checkin:10} | {reason:30}"
		rows.append(row)
	table_text = header + "\n".join(rows)
	return table_text


def get_all_active_employees():
	"""
	Fetch all employees from ERPNext where status is 'Active'.
	Returns a list of dictionaries with 'user_id' mapping to Clockify user ID.
	"""
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["user_id", "name"]
	)
	return employees


def send_compliance_report(non_compliant, today_str):
	target = get_compliance_channel()  # Management Channel
	if non_compliant:
		# Build a plain text header
		header_text = f"📢 Daily Clockify Compliance Report – {today_str}\n"
		header_text += f"Total Non-Compliant Employees: {len(non_compliant)}\n\n"
		# Build the table as a code block
		report_message = build_compliance_report_table(non_compliant)
		# Ensure message is within Slack's 4000-character limit
		split_messages = split_long_message(header_text + report_message)

	else:
		# Build a plain text message
		split_messages = [f"📢 Daily Clockify Compliance Report – {today_str}\nAll employees are compliant with Clockify logs for today."]

	for msg in split_messages:
		send_slack_message_for_employee([target], msg)

def get_compliance_channel():
	"""
	Fetches the 'operation_compliance_channel' from System Settings.
	Logs an error and aborts if the field or value is missing.
	"""
	try:
		channel = frappe.db.get_single_value("System Settings", "operation_compliance_channel")
	except Exception:
		frappe.log_error("Failed to fetch Operation Compliance Channel", "Operation Compliance Channel")
		frappe.throw(_("System Settings or the field operation_compliance_channel is missing."))
	if not channel:
		frappe.log_error("No Operation Compliance Channel configured", "Configuration Error")
		frappe.throw(_("Please configure Operation Compliance Channel in System Settings."))
	return channel

def split_long_message(message, max_length=3800):
	"""
	Splits long Slack messages into multiple parts while keeping code block formatting.
	"""
	messages = []
	while len(message) > max_length:
		split_index = message[:max_length].rfind("\n")  # Split at the last newline
		if split_index == -1:
			split_index = max_length  # If no newline found, force split

		# Ensure code block formatting stays intact
		messages.append(f"```{message[:split_index]}```")
		message = message[split_index:].strip()

	# Add the last part
	if message:
		messages.append(f"```{message}```")

	return messages


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


def fetch_clockify_workspace_users(api_key, workspace_ids, active_employees):
	"""
	Loops over each workspace ID and calls the Clockify API to fetch users.
	Returns a dictionary with workspace ID as key and list of users as value.
	"""
	employee_records = {}
	headers = {"X-Api-Key": api_key}
	# Convert active_employees list into a dictionary for quick lookups
	active_employee_map = {emp["user_id"].lower(): emp["name"] for emp in active_employees}
	for ws in workspace_ids:
		url = f"https://api.clockify.me/api/v1/workspaces/{ws}/users?page-size=500"
		try:
			response = requests.get(url, headers=headers)
			response.raise_for_status()
			users = response.json()
			for user in users:
				email = user.get("email")
				if not email or email not in active_employee_map:
					# print(f"User {user.get('name')} missing email")
					continue
				if email in employee_records:
					if ws not in employee_records[email]["workspace_ids"]:
						employee_records[email]["workspace_ids"].append(ws)
				else:
					# create a new record
					employee_records[email] = {
						"workspace_ids": [ws],
						"user_id": user.get("id"),
						"employee_name": user.get("name"),
						"employee": active_employee_map[email]
					}
		except Exception as e:
			frappe.log_error(
				f"Error fetching Clockify users for workspace {ws} as clockify key {api_key} not in workspace",
				"Clockify Task")
			continue

	return employee_records

def is_public_holiday(date):
	"""
	:param date: string YYYY-MM-DD
	:returns: True if `date` is in this year’s Holiday List, False otherwise
	"""
	# 1) get all Holiday List names whose from_date is in “this year”
	holiday_lists = frappe.db.get_list(
		"Holiday List",
		filters = {
			"from_date": ["timespan", "this year"]
		},
		pluck = "name"
	) 

	if not holiday_lists:
		return False

	# 2) pick the first matching Holiday List
	holiday_list = holiday_lists[0]

	# 3) check if `date` appears in its Holiday child table
	return frappe.db.exists(
		"Holiday",
		{"parent": holiday_list, "holiday_date": date}
	)


# send compliance report to operations channel
def send_daily_compliance_report():
	"""
	End-of-Day Compliance Report (to be run at 11 PM):
	- Checks employee check-ins and Clockify logs to determine compliance.
	- Sends a compliance report to Slack.
	"""
	today_str, _, _, start_dt, end_dt = get_today_date_range()
	today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

	# Skip public holidays
	if is_public_holiday(today_date):
		return
	if today_date.weekday() in (5, 6):  # Skip weekends
		return

	# Get system-level Clockify settings (API Key and comma-separated workspace IDs)
	custom_api_key, workspace_ids = get_system_clockify_settings()
	if not custom_api_key or not workspace_ids:
		frappe.log_error("Missing Clockify API Key or Workspace IDs in System Settings", "Clockify Task")
		# print("Missing Clockify API Key or Workspace IDs in System Settings")
		return

	# Fetch active employees from ERPNext
	active_employees = get_all_active_employees()
	# fetch  users across all workspaces
	workspace_users = fetch_clockify_workspace_users(custom_api_key, workspace_ids, active_employees)

	non_compliant = []
	for email, emp_data in workspace_users.items():
		checkin, checkout, reason = check_non_compliance(email, emp_data, custom_api_key, start_dt, end_dt)
		if reason:
			non_compliant.append({
				"employee_id": emp_data["employee"],
				"employee": emp_data["employee_name"],
				"checkin": checkin,
				"checkout": checkout,
				"reason": reason
			})
	# send to erp
	create_employee_compliance_reports(non_compliant, report_date=today_str)
	# Send to channel at 9am nextday

def send_yesterday_compliance_report_to_slack():
	from datetime import datetime, timedelta
	today = datetime.today().date() # 2025-04-25
	if is_public_holiday(today) or today.weekday() in (5, 6):
		return
	
	# Start from yesterday and go backwards to find last valid working day
	yesterday_date = today - timedelta(days=1)
	while yesterday_date.weekday() in (5, 6) or is_public_holiday(yesterday_date):
		yesterday_date -= timedelta(days=1)

	# Convert to string to keep your variable consistent
	yesterday = yesterday_date.strftime("%Y-%m-%d")

	# Pull non-compliant data from your DocType for yesterday
	records = frappe.get_all("Employee Compliance Report", 
		filters={
			"report_date": yesterday,
		},
		fields=["employee", "employee_name", "checkin", "checkout", "reason"],
		limit_page_length=0 
	)

	# Format the data like original non_compliant list
	non_compliant = [{
		"employee_id": r.employee,
		"employee": r.employee_name,
		"checkin": format_time_string(r.checkin),
		"checkout": format_time_string(r.checkout),
		"reason": r.reason
	} for r in records]

	send_compliance_report(non_compliant, yesterday)

def format_time_string(value):
	if value and value.upper() != "N/A":
		try:
			# If it's a full datetime string
			dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
			return dt.strftime("%I:%M %p")
		except (ValueError, AttributeError):
			return value  # fallback in case of unexpected format
	return "N/A"


def get_employee_checkin(emp_email, start_dt, end_dt):
	"""
	Fetches the check-in and check-out records for a specific employee within the given time range.
	Returns a dictionary with keys "checkin" and "checkout". If no records are found, returns an empty dict.

	Parameters:
		emp_email (str): The employee's email (used as user_id in ERPNext).
		start_dt (datetime): The start of the time range.
		end_dt (datetime): The end of the time range.
	"""
	# Lookup the ERPNext Employee using the email stored as user_id
	employee = frappe.db.get_value("Employee", {"user_id": emp_email}, "name")

	if not employee:
		print("Employee not found in ERPNext for email:", emp_email)
		return {}  # Employee not found in ERPNext

	# Fetch all check-in and check-out records for this employee within the time range
	checkin_records = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [start_dt, end_dt]]
		},
		fields=["time", "log_type"],
		order_by="time asc"
	)

	emp_checkins = {}
	# Assuming the earliest "IN" is the check-in and the latest "OUT" is the checkout
	for record in checkin_records:
		if record.get("log_type") == "IN" and "checkin" not in emp_checkins:
			emp_checkins["checkin"] = record.get("time")
		elif record.get("log_type") == "OUT":
			# Continuously update checkout so the last record remains as the latest checkout
			emp_checkins["checkout"] = record.get("time")

	return emp_checkins

def get_employee_shift_type(employee_id):
	"""
	Fetches the shift type for an employee by filtering for only the Submitted shifts.
	Returns the shift type of the first assignment found.
	"""
	# Query for the employee's shift assignment, filtering for only "Submitted" shifts
	shift_assignments = frappe.get_all("Shift Assignment", filters={
		"employee": employee_id,  # Use employee ID field from emp_data["employee"]
		"docstatus": 1,  # Ensure we're only fetching submitted shifts
		"status": "Active"  # Ensure we're only fetching active shifts
	}, fields=["shift_type"], limit=1)  # Limit to the first assignment

	# If no active submitted shift assignment found, return None
	if not shift_assignments:
		return None
	# Return the shift type of the first valid submitted assignment
	return shift_assignments[0]["shift_type"]

def check_non_compliance(emp_email, emp_data, api_key, start_dt, end_dt):
	"""
	Determines the reason for an employee's non-compliance based on ERPNext check-ins and Clockify logs.
	Returns a tuple: (checkin_time, checkout_time, non_compliance_reason)

	- If an active timer is running, we consider the employee compliant (i.e. return no reason).
	- If there is no check-in but Clockify logs exist, returns "Clockify logs present but no check-in recorded".
	- If no check-in and no logs, returns "No check-in, No Clockify logs, No leave recorded" (unless on leave).
	- If total logged time is below required hours, returns the underworked message.
	"""
	employee_id = emp_data.get("employee")
	# Validate required Clockify details
	if not emp_data.get("user_id") or not emp_data.get("workspace_ids"):
		return None, None, "Missing Clockify API User ID or Workspace ID"

	user_id = emp_data["user_id"]
	workspaces = emp_data["workspace_ids"]

	# Fetch employee check-ins (from ERPNext) using the unique identifier (email here)
	emp_checkins = get_employee_checkin(emp_email, start_dt, end_dt)
	checkin_time = emp_checkins.get("checkin")
	checkout_time = emp_checkins.get("checkout")
	# Check if an active timer is running in any workspace
	try:
		if any(is_clockify_timer_active(api_key, ws, user_id) for ws in workspaces):
			# If there's an active timer, we assume the employee is compliant for now.
			return None, None, None
	except Exception as e:
		# print("Error checking active timer for {emp_email}")
		frappe.log_error(f"Error checking active timer for {emp_email}", "Clockify Compliance Check")
		return checkin_time, checkout_time, "Invalid API Key in the system"
	# Continue to process further if the timer check fails

	# Fetch Shift Type linked to the employee (using the first shift found)
	shift_type = get_employee_shift_type(employee_id)
	if not shift_type:
		# If no shift assigned or no valid shift found, skip the compliance check
		return None,None, None

	# Fetch shift type thresholds
	try:
		shift_type_doc = frappe.get_doc("Shift Type", shift_type)
		# Assuming only full day hours is stored,
		# calculate half day hours by dividing the full day working hours by 2.
		working_hours_full_day = shift_type_doc.working_hours_threshold_for_full_day
		working_hours_half_day = working_hours_full_day / 2
	except Exception as e:
		frappe.log_error(f"Error fetching Shift Type data for {employee_id}", "Shift Type Fetch Error")
		return checkin_time, checkout_time, "Error fetching Shift Type data"

	FULL_DAY_SECONDS = working_hours_full_day * 3600  # Convert full day hours to seconds
	HALF_DAY_SECONDS = working_hours_half_day * 3600      # Convert half day hours to seconds

	# Fetch Clockify logs (total logged time across all workspaces)
	try:
		total_logged_seconds = sum(
			sum_clockify_durations(get_clockify_time_entries(api_key, ws, user_id, start_dt, end_dt))
			for ws in workspaces
		)
		hours, minutes = divmod(total_logged_seconds // 60, 60)
	except Exception as e:
		# print("Clockify API sum log error for {emp_email}")
		frappe.log_error(f"Clockify API error for {emp_email}", "Clockify Compliance Check")
		return checkout_time, checkout_time, "Invalid API Key in the system"

	# Get leave status from ERPNext (e.g., "On Leave", "Half Day", etc.)
	# Here, we assume get_employee_leave_status takes ERPNext Employee ID and a date
	leave_status = get_employee_leave_status(emp_data["employee"], start_dt.date())
	min_seconds = HALF_DAY_SECONDS if leave_status == "Half Day" else FULL_DAY_SECONDS
	half_day_message = " (Half Day)" if min_seconds == HALF_DAY_SECONDS else ""

	# Compliance Checks
	if leave_status == "On Leave":
		return None, None, None  # No non-compliance when on leave

	if not checkin_time:
		if total_logged_seconds > 0:
			return None, None, f"No check-in recorded. {hours} hr {minutes} mins logged{half_day_message}"
		else:
			# No check-in, No Clockify logs, No leave recorded
			return None, None, "Absent"

	if total_logged_seconds == 0:
		return checkin_time, checkout_time, "No Clockify logs recorded"

	if total_logged_seconds < min_seconds:
		return checkin_time, checkout_time, f"Only {hours} hr {minutes} mins logged{half_day_message}"

	# If all checks pass, the employee is compliant
	return checkin_time, checkout_time, None


def get_employee_leave_status(emp_id, date):
	"""
	Fetches the leave status for an employee on a given date.

	:param emp_id: Employee ID
	:param date: The date to check leave status (YYYY-MM-DD)
	:return: "On Leave", "Half Day", or None if not on leave.
	"""
	return frappe.get_value("Attendance", {"employee": emp_id, "attendance_date": date, "docstatus": ["!=", 2]}, "status")

def create_employee_compliance_reports(entries, report_date=None):
	"""
	entries: list of dicts with keys:
	  - employee    (Link to Employee)
	  - checkin     (Time string, e.g. "09:00")
	  - checkout    (Time string, e.g. "17:00")
	  - reason      (str)
	report_date: date string "YYYY-MM-DD";
	"""
	for e in entries:
		# build the new document
		doc = frappe.get_doc({
			"doctype": "Employee Compliance Report",
			"employee": e["employee_id"],
			"report_date": report_date,
			"checkin": e["checkin"],
			"checkout": e["checkout"],
			"reason": e["reason"]
		})
		# insert into the database
		doc.insert(ignore_permissions=True)

	# commit once after all inserts
	frappe.db.commit()


# weekly employee clockify summary report
def send_weekly_time_report():
	"""
	Generates and sends a weekly time tracking report for all employees.
	The report includes daily time logged, breakdown by task category, missing days,
	and total vs expected hours.
	"""
	# Get system-level Clockify settings
	custom_api_key, workspace_ids = get_system_clockify_settings()
	if not custom_api_key or not workspace_ids:
		frappe.log_error("Missing Clockify API Key or Workspace IDs in System Settings", "Clockify Task")
		return

	# Calculate date range for last week
	end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
	start_date = end_date - timedelta(days=7)
	# Get all active employees
	active_employees = get_all_active_employees()
	# Fetch users across all workspaces
	workspace_users = fetch_clockify_workspace_users(custom_api_key, workspace_ids, active_employees)
	public_holidays_in_week = get_public_holidays_in_week(start_date, end_date)

	# Count public holidays
	public_holidays_in_week_count = sum(1 for v in public_holidays_in_week.values() if v)
	# Calculate working days by excluding weekends and public holidays
	working_days_in_week = 7 - public_holidays_in_week_count

	# Process each employee's time entries in all workspaces
	employee_reports = collect_employee_reports(
		workspace_users,
		start_date,
		end_date,
		custom_api_key,
		public_holidays_in_week,
		working_days_in_week
	)

	# Send reports via email and Slack
	for report in employee_reports:
		# Format email content
		email_content = generate_email_content(report)
		recipients = [report['email']]
		if report.get('team_lead'):
			recipients.append(report['team_lead'])
		# Send email as HTML
		frappe.sendmail(
			recipients=recipients,
			subject=f"Weekly Time Tracking Report for {report['employee_name']}",
			message=email_content,
			now=True,
		)

		# Send Slack message
		if report['email']:
			slack_message = html_to_slack_plaintext(email_content)
			messages = split_long_message(slack_message)
			for message in messages:
				send_slack_message_for_employee([report['email']], message)

def process_employee_workspaces(emp_data, start_date, end_date, custom_api_key, public_holidays_in_week, working_days_in_week):
	"""
	Process all workspaces for a single employee and generate their reports.
	
	Args:
		emp_data (dict): Employee data containing user_id, workspace_ids, etc.
		start_date (datetime): Start date for the report
		end_date (datetime): End date for the report
		custom_api_key (str): Clockify API key
		public_holidays_in_week (dict): Dictionary of public holidays
		working_days_in_week (int): Number of working days in the week
		
	Returns:
		tuple: (employee_weekly_reports, late_entries_count, wfh_days_count)
	"""
	if not emp_data.get("user_id") or not emp_data.get("workspace_ids"):
		return None, None, None

	user_id = emp_data["user_id"]
	workspaces = emp_data["workspace_ids"]
	
	# Get employee's shift type for expected hours
	shift_type = get_employee_shift_type(emp_data["employee"])
	if not shift_type:
		return None, None, None
		
	try:
		shift_type_doc = frappe.get_doc("Shift Type", shift_type)
		expected_hours = shift_type_doc.working_hours_threshold_for_full_day
	except Exception:
		return None, None, None
	
	# Initialize the employees weekly report for each workspace
	employee_weekly_reports = []

	# Fetch and process Late Entries in a week
	late_entries_count = get_late_entries_count(emp_data["employee"], start_date, end_date - timedelta(days=1))

	# Fetch and process Work From Home in a week
	wfh_days_count = get_wfh_days_count(emp_data["employee"], start_date, end_date - timedelta(days=1))

	# Process each workspace
	for workspace_id in workspaces:
		task_id = get_clockify_report_task_id(workspace_id, start_date, end_date - timedelta(days=1), user_id, custom_api_key)
		if task_id:
			report_data = get_clockify_report_result(workspace_id, task_id, custom_api_key)
			if report_data:
				totals = report_data.get("totals", {})
				chart = report_data.get("chart", {})
				group_one = report_data.get("groupOne", {})
			
				# Process report data
				weekly_report = generate_weekly_report(
					start_date, 
					end_date - timedelta(days=1), 
					emp_data["employee"], 
					user_id, 
					totals, 
					chart, 
					group_one, 
					expected_hours, 
					public_holidays_in_week, 
					working_days_in_week
				)
				employee_weekly_reports.append({
					"workspace_id": workspace_id,
					"weekly_report": weekly_report
				})
	
	return employee_weekly_reports, late_entries_count, wfh_days_count

def collect_employee_reports(workspace_users, start_date, end_date, custom_api_key, public_holidays_in_week, working_days_in_week):
	"""
	Collect reports for all employees from their respective workspaces.
	
	Args:
		workspace_users (dict): Dictionary of workspace users
		start_date (datetime): Start date for the report
		end_date (datetime): End date for the report
		custom_api_key (str): Clockify API key
		public_holidays_in_week (dict): Dictionary of public holidays
		working_days_in_week (int): Number of working days in the week
		
	Returns:
		list: List of employee reports
	"""
	employee_reports = []
	
	for email, emp_data in workspace_users.items():
		employee_weekly_reports, late_entries_count, wfh_days_count = process_employee_workspaces(
			emp_data, 
			start_date, 
			end_date, 
			custom_api_key, 
			public_holidays_in_week, 
			working_days_in_week
		)
		team_lead = frappe.db.get_value("Employee", {"user_id": email}, "team_lead")
		if employee_weekly_reports:
			employee_reports.append({
				"employee_name": emp_data["employee_name"],
				"email": email,
				"team_lead": team_lead,
				"weekly_reports": employee_weekly_reports,
				"late_entries_count": late_entries_count,
				"wfh_days_count": wfh_days_count
			})
	
	return employee_reports

def generate_summary_section(weekly_report, report):
	"""Generate the summary section of the report (hours, late entries, leaves, WFH)"""
	content = ""
	content += f"<strong>Total Hours:</strong> {weekly_report['total_hours']:.2f}<br>"
	content += f"<strong>Expected Hours:</strong> {weekly_report['expected_hours']:.2f}<br>"
	content += f"<strong>Total Late Entries:</strong> {report['late_entries_count'] if report['late_entries_count'] else 0}<br>"
	
	# Calculate leaves
	leave_count = sum(1 if day.get("leave_status") == "On Leave" else 0.5 if day.get("leave_status") == "Half Day" else 0 
					for day in weekly_report.get("week_breakdown", []))
	content += f"<strong>Total Leaves Taken:</strong> {leave_count:.1f}<br>"
	content += f"<strong>Total WFH Days:</strong> {report['wfh_days_count'] if report['wfh_days_count'] else 0}<br>"
	return content

def generate_daily_breakdown_section(weekly_report):
	"""Generate the daily breakdown section of the report"""
	content = "<br><strong>Daily Breakdown:</strong><br>"
	for day in weekly_report.get("week_breakdown", []):
		date = day["date"]
		total_time = day["total_time"]
		formatted_date = date.strftime("%A, %B %d, %Y")
		leave = f" ({day['leave_status']})" if day['leave_status'] in ["On Leave", "Half Day"] else ""
		public_holiday = f" (Public Holiday)" if day['is_public_holiday'] else ""
		content += f"<strong>{formatted_date}</strong>: {total_time:.2f} hours{leave}{public_holiday}<br>"

		for project in day.get("projects", []):
			content += f"- Project: <strong>{project['project_name']}</strong> — Time Spent: {project['time_spent']:.2f} hours<br>"
	return content

def generate_missing_days_section(weekly_report):
	"""Generate the missing days section of the report"""
	content = ""
	if weekly_report.get("missing_days"):
		content += "<br><strong>Missing Days:</strong><br>"
		for missing_date in weekly_report["missing_days"]:
			formatted_date = missing_date.strftime("%A, %B %d, %Y")
			content += f"{formatted_date}<br>"
	return content

def generate_project_breakdown_section(weekly_report):
	"""Generate the project breakdown section of the report"""
	content = "<br><strong>Project Breakdown:</strong><br>"
	for project in weekly_report.get("project_breakdown", []):
		content += f"<strong>Project:</strong> {project['project_name']} — <strong>Total Duration:</strong> {project['total_duration']:.2f} hours<br>"
		for task in project.get("tasks", []):
			content += f"— Task: {task['task_name']} — Duration: {task['task_duration']:.2f} hours<br>"
	return content

def generate_email_content(report):
	"""Generate the complete email content for a report"""
	email_content = f"<h3>Weekly Time Tracking Report for {report['employee_name']}</h3><br>"

	for workspace_report in report.get("weekly_reports", []):
		weekly_report = workspace_report["weekly_report"]
		
		# Generate each section
		email_content += generate_summary_section(weekly_report, report)
		email_content += generate_daily_breakdown_section(weekly_report)
		email_content += generate_missing_days_section(weekly_report)
		email_content += generate_project_breakdown_section(weekly_report)

	return email_content

def generate_weekly_report(start_date, end_date, employee_id, user_id, totals, chart, group_one, expected_hours, public_holidays_in_week, working_days_in_week):
	"""
	Generate the weekly report by processing totals, chart, and other data for each employee.
	"""
	weekly_report = {
		"total_hours": totals[0]["totalTime"] / 3600,  # Convert seconds to hours
		"expected_hours": expected_hours * working_days_in_week,
		"week_breakdown": [],
		"project_breakdown": [],
		"missing_days": [],
	}

	# Process the daily breakdown from the chart
	week_breakdown, missing_days = process_daily_breakdown(
		start_date, end_date, employee_id, chart, public_holidays_in_week
	)
	weekly_report["week_breakdown"] = week_breakdown
	weekly_report["missing_days"] = missing_days

	# Process the project breakdown
	weekly_report["project_breakdown"] = process_project_breakdown(group_one, user_id)

	return weekly_report

def process_project_breakdown(group_one, user_id):
	"""
	Process project breakdown from Clockify data.
	
	Args:
		group_one (list): List of projects from Clockify
		user_id (str): Clockify user ID
		
	Returns:
		list: List of project information with tasks
	"""
	project_breakdown = []
	
	for project in group_one:
		project_info = {
			"project_name": project["name"],
			"total_duration": 0,
			"tasks": []
		}

		for task in project.get("children", []):
			task_duration = 0
			for child in task.get("children", []):
				if child["_id"] == user_id:
					task_duration = child["duration"] / 3600
					break  # stop after finding user's entry
			task_info = {
				"task_name": task.get("name", "Unnamed"),
				"task_duration": task_duration
			}
			project_info["tasks"].append(task_info)
			project_info["total_duration"] += task_duration

		project_breakdown.append(project_info)
		
	return project_breakdown

def process_daily_breakdown(start_date, end_date, employee_id, chart, public_holidays_in_week):
	"""
	Process daily breakdown for the week.
	
	Args:
		start_date (datetime): Start date of the week
		end_date (datetime): End date of the week
		employee_id (str): Employee ID
		chart (dict): Clockify chart data
		public_holidays_in_week (dict): Dictionary of public holidays
		
	Returns:
		tuple: (week_breakdown list, missing_days list)
	"""
	week_breakdown = []
	missing_days = []
	current_date = start_date
	
	while current_date <= end_date:
		leave_status = get_employee_leave_status(employee_id, current_date.date())
		date_str = current_date.strftime("%Y-%m-%d")
		daily_entries = chart.get(date_str, [])
		day_total_time = sum(entry["totalTime"] for entry in daily_entries) / 3600  # in hours

		is_public_holiday = public_holidays_in_week.get(current_date.date())
		# Mark as missing if < 1 hour and not a public holiday and no leave applied
		if day_total_time < 1 and is_public_holiday is None and current_date.weekday() < 5 and leave_status not in ["On Leave", "Half Day"]:
			missing_days.append(current_date)

		# Build daily report (0-hour days included)
		daily_report = {
			"date": current_date,
			"total_time": day_total_time,
			"leave_status": leave_status,
			"is_public_holiday": True if is_public_holiday is not None and current_date.weekday() < 5 else False,
			"projects": [],
		}

		for entry in daily_entries:
			daily_report["projects"].append({
				"project_name": entry["projectName"],
				"time_spent": entry["totalTime"] / 3600
			})

		# Only add weekends if hours > 0
		if day_total_time > 0 or (current_date.weekday() < 5):  
			week_breakdown.append(daily_report)

		current_date += timedelta(days=1)
		
	return week_breakdown, missing_days

def html_to_slack_plaintext(html):
	# Replace <br> with newline
	text = html.replace("<br>", "\n")

	# Replace <strong> and other tags with nothing
	text = re.sub(r"<[^>]+>", "", text)

	# Remove multiple consecutive blank lines
	text = re.sub(r"\n\s*\n+", "\n\n", text.strip())

	return text

def get_public_holidays_in_week(start_date, end_date):
	"""
	Returns a dictionary with dates as keys and whether they are public holidays as values.
	"""
	public_holidays = {}
	current_date = start_date
	while current_date < end_date:
		public_holidays[current_date.date()] = is_public_holiday(str(current_date))

		current_date += timedelta(days=1)
	return public_holidays

def get_clockify_report_task_id(workspace_id, start_date, end_date, user_id, api_key):
	url = f"https://app.clockify.me/report/workspaces/{workspace_id}/async/reports/summary"
	
	headers = {
		"Content-Type": "application/json",
		"X-Api-Key": api_key
	}
	payload = {
		"dateRangeStart": start_date.strftime("%Y-%m-%dT00:00:00.000Z"),
		"dateRangeEnd": end_date.strftime("%Y-%m-%dT23:59:59.999Z"),
		"sortOrder": "DESCENDING",
		"description": "",
		"rounding": False,
		"withoutDescription": False,
		"amounts": [],
		"amountShown": "HIDE_AMOUNT",
		"zoomLevel": "WEEK",
		"userLocale": "en-US",
		"customFields": None,
		"userCustomFields": None,
		"kioskIds": [],
		"users": {
			"contains": "CONTAINS",
			"ids": [user_id],
			"status": "ACTIVE_WITH_PENDING",
			"numberOfDeleted": 0
		},
		"userGroups": {
			"contains": "CONTAINS",
			"ids": [],
			"status": "ACTIVE_WITH_PENDING",
			"numberOfDeleted": 0
		},
		"summaryFilter": {
			"sortColumn": "DURATION",
			"groups": ["PROJECT", "TASK", "USER"],
			"summaryChartType": "PROJECT"
		}
	}

	response = requests.post(url, headers=headers, json=payload)
	if response.status_code == 202:
		return response.json().get("reportTaskId")
	else:
		error_message = f"Error: {response.status_code} - {response.text}"
		frappe.log_error(message=error_message, title="Clockify Report Task ID Fetch Error for Clockify Summary Report")
		return None
	

import time
def get_clockify_report_result(workspace_id, report_task_id, api_key, max_retries=3, delay=1.5):
	url = f"https://app.clockify.me/report/workspaces/{workspace_id}/async/reports/summary/{report_task_id}"
	
	headers = {
		"X-Api-Key": api_key
	}

	for attempt in range(max_retries):
		try:
			response = requests.get(url, headers=headers)

			if response.status_code == 200:
				return response.json()
			elif response.status_code == 202:
				time.sleep(delay)
			else:
				error_message = f"Error: {response.status_code} - {response.text}"
				frappe.log_error(message=error_message, title="Clockify Report Fetch Error for Clockify Summary Report")
				return None
		except requests.exceptions.RequestException as e:
			error_message = f"Request failed: {e}"
			frappe.log_error(message=error_message, title="Clockify Report Fetch Error for Clockify Summary Report")
			time.sleep(delay)

	error_message = "Max retries reached. Report is still not ready."
	frappe.log_error(message=error_message, title="Clockify Report Fetch Error for Clockify Summary Report")
	return None

def get_late_entries_count(employee_id, start_date, end_date):
	late_entries = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee_id,
			"late_entry": 1,
			"docstatus": ["!=", 2],
			"attendance_date": ["between", [start_date.date(), end_date.date()]]
		},
		fields=["name"]
	)
	return len(late_entries)

def get_wfh_days_count(employee_id, start_date, end_date):
	wfh_days = frappe.get_all(
		"Work From Home",
		filters={
			"employee": employee_id,
			"docstatus": ["!=", 2],
			"status": "Approved",
			"from_date": ["between", [start_date.date(), end_date.date()]]
		},
		fields=["total_days"]
	)
	return sum(day["total_days"] for day in wfh_days)
	
	



