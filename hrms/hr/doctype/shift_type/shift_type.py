# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


from datetime import datetime, timedelta
from itertools import groupby

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, create_batch, get_datetime, get_time, getdate, time_diff
from frappe.integrations.doctype.slack_webhook_url.slack_webhook_url import send_slack_message
from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday

from hrms.hr.doctype.attendance.attendance import mark_attendance
from hrms.hr.doctype.employee_checkin.employee_checkin import (
	calculate_working_hours,
	mark_attendance_and_link_log,
	get_employee_leave_status,
	send_slack_message_for_employee,
	get_employee_clockify_details,
	is_clockify_timer_active,
	get_clockify_time_entries
)
from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift, get_shift_details
from hrms.utils import get_date_range
from hrms.utils.holiday_list import get_holiday_dates_between

EMPLOYEE_CHUNK_SIZE = 50

frappe.utils.logger.set_log_level("DEBUG")
logger = frappe.logger("shift_type", allow_site=True, file_count=10)


class ShiftType(Document):
	def validate(self):
		start = get_time(self.start_time)
		end = get_time(self.end_time)
		self.validate_same_start_and_end(start, end)
		self.validate_circular_shift(start, end)
		self.validate_unlinked_logs()

	def validate_same_start_and_end(self, start_time: datetime.time, end_time: datetime.time):
		if start_time == end_time:
			frappe.throw(
				title=_("Invalid Shift Times"),
				msg=_("Start time and end time cannot be same."),
			)

	def validate_circular_shift(self, start_time: datetime.time, end_time: datetime.time):
		shift_start, shift_end = self.get_shift_start_and_shift_end(start_time, end_time)
		if self.get_total_shift_duration_in_minutes(shift_start, shift_end) >= 1440:
			max_label = self.get_max_shift_buffer_label()
			frappe.throw(
				title=_("Invalid Shift Times"),
				msg=_("Please reduce {0} to avoid shift time overlapping with itself").format(
					frappe.bold(max_label)
				),
			)

	def get_shift_start_and_shift_end(
		self, start_time: datetime.time, end_time: datetime.time
	) -> tuple[datetime]:
		shift_start = datetime.combine(getdate(), start_time)
		if start_time < end_time:
			shift_end = datetime.combine(getdate(), end_time)
		elif start_time > end_time:
			shift_end = datetime.combine(add_days(getdate(), 1), end_time)
		return shift_start, shift_end

	def get_total_shift_duration_in_minutes(
		self, shift_start: datetime.time, shift_end: datetime.time
	) -> int:
		return (
			(round(time_diff(shift_end, shift_start).total_seconds() / 60))
			+ self.allow_check_out_after_shift_end_time
			+ self.begin_check_in_before_shift_start_time
		)

	def get_max_shift_buffer_label(self) -> str:
		labels = {
			self.meta.get_label(
				"allow_check_out_after_shift_end_time"
			): self.allow_check_out_after_shift_end_time,
			self.meta.get_label(
				"begin_check_in_before_shift_start_time"
			): self.begin_check_in_before_shift_start_time,
		}
		return max(labels, key=labels.get)

	def validate_unlinked_logs(self):
		if self.is_field_modified("start_time") and self.unlinked_checkins_exist():
			frappe.throw(
				title=_("Unmarked Check-in Logs Found"),
				msg=_("Mark attendance for existing check-in/out logs before changing shift settings"),
			)

	def is_field_modified(self, fieldname):
		return not self.is_new() and self.has_value_changed(fieldname)

	def unlinked_checkins_exist(self):
		return frappe.db.exists(
			"Employee Checkin",
			{"shift": self.name, "attendance": ["is", "not set"], "skip_auto_attendance": 0, "offshift": 0},
		)

	@frappe.whitelist()
	def process_auto_attendance(self):
		if (
			not cint(self.enable_auto_attendance) or not self.process_attendance_after
			# or not self.last_sync_of_checkin
		):
			logger.info(
				f"Skipping Shift due to; Auto-Attendance: {cint(self.enable_auto_attendance)}, Process Attendance After: {self.process_attendance_after}, Last Sync of Checkin: {self.last_sync_of_checkin}"
			)
			return

		logs = self.get_employee_checkins()
		group_key = lambda x: (x["employee"], x["shift_start"])  # noqa
		for key, group in groupby(sorted(logs, key=group_key), key=group_key):
			single_shift_logs = list(group)
			attendance_date = key[1].date()
			employee = key[0]

			if not self.should_mark_attendance(employee, attendance_date):
				logger.info(f"Skipping Attendance due to holiday. {employee}-{attendance_date}")
				continue

			(
				attendance_status,
				working_hours,
				late_entry,
				early_exit,
				in_time,
				out_time,
			) = self.get_attendance(single_shift_logs)
			mark_attendance_and_link_log(
				single_shift_logs,
				attendance_status,
				attendance_date,
				working_hours,
				late_entry,
				early_exit,
				in_time,
				out_time,
				self.name,
			)

		# commit after processing checkin logs to avoid losing progress
		frappe.db.commit()  # nosemgrep

		assigned_employees = self.get_assigned_employees(self.process_attendance_after, True)
		# mark absent in batches & commit to avoid losing progress since this tries to process remaining attendance
		# right from "Process Attendance After" to "Last Sync of Checkin"
		for batch in create_batch(assigned_employees, EMPLOYEE_CHUNK_SIZE):
			for employee in batch:
				self.mark_absent_for_dates_with_no_attendance(employee)

			frappe.db.commit()  # nosemgrep

	def get_employee_checkins(self) -> list[dict]:
		return frappe.get_all(
			"Employee Checkin",
			fields=[
				"name",
				"employee",
				"log_type",
				"time",
				"shift",
				"shift_start",
				"shift_end",
				"shift_actual_start",
				"shift_actual_end",
				"device_id",
			],
			filters={
				"skip_auto_attendance": 0,
				"attendance": ("is", "not set"),
				"time": (">=", self.process_attendance_after),
				"shift_actual_end": ("<", getdate(self.last_sync_of_checkin)),
				"shift": self.name,
				"offshift": 0,
			},
			order_by="employee,time",
		)

	def get_attendance(self, logs):
		"""Return attendance_status, working_hours, late_entry, early_exit, in_time, out_time
		for a set of logs belonging to a single shift.
		Assumptions:
		1. These logs belongs to a single shift, single employee and it's not in a holiday date.
		2. Logs are in chronological order
		"""
		late_entry = early_exit = False
		total_working_hours, in_time, out_time = calculate_working_hours(
			logs, self.determine_check_in_and_check_out, self.working_hours_calculation_based_on
		)
		if (
			cint(self.enable_late_entry_marking)
			and in_time
			and in_time > logs[0].shift_start + timedelta(minutes=cint(self.late_entry_grace_period))
		):
			late_entry = True

		if (
			cint(self.enable_early_exit_marking)
			and out_time
			and out_time < logs[0].shift_end - timedelta(minutes=cint(self.early_exit_grace_period))
		):
			early_exit = True

		if (
			self.working_hours_threshold_for_absent
			and total_working_hours < self.working_hours_threshold_for_absent
		):
			return "Absent", total_working_hours, late_entry, early_exit, in_time, out_time

		if (
			self.working_hours_threshold_for_half_day
			and total_working_hours < self.working_hours_threshold_for_half_day
		):
			return "Half Day", total_working_hours, late_entry, early_exit, in_time, out_time

		return "Present", total_working_hours, late_entry, early_exit, in_time, out_time

	def mark_absent_for_dates_with_no_attendance(self, employee: str):
		"""Marks Absents for the given employee on working days in this shift that have no attendance marked.
		The Absent status is marked starting from 'process_attendance_after' or employee creation date.
		"""
		start_time = get_time(self.start_time)
		dates = self.get_dates_for_attendance(employee)
		logger.info(f"Dates = {dates}")

		for date in dates:
			logger.info(f"Getting employee {employee} shift having data: {date} {start_time}")
			timestamp = datetime.combine(date, start_time)
			shift_details = get_employee_shift(employee, timestamp, True)
			if shift_details and shift_details.shift_type.name == self.name:
				logger.info(f"Shift Assigned {shift_details} and employee {employee}")
				logger.info(f"Going to mark attendance date: {date} as Absent due to no attendance")
				attendance = mark_attendance(employee, date, "Absent", self.name)
				logger.info(f"Attendance marked: {attendance}")
				if not attendance:
					continue

				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": "Attendance",
						"reference_name": attendance,
						"content": frappe._("Employee was marked Absent due to missing Employee Checkins."),
					}
				).insert(ignore_permissions=True)

	def get_dates_for_attendance(self, employee: str) -> list[str]:
		start_date, end_date = self.get_start_and_end_dates(employee)
		logger.info(
			f"Employee: {employee}, Attendance Start Date: {start_date}, Attendance End Date: {end_date}"
		)

		# no shift assignment found, no need to process absent attendance records
		if start_date is None:
			return []

		date_range = get_date_range(start_date, end_date)

		# skip marking absent on holidays
		holiday_list = self.get_holiday_list(employee)
		holiday_dates = get_holiday_dates_between(holiday_list, start_date, end_date)
		# skip dates with attendance
		marked_attendance_dates = self.get_marked_attendance_dates_between(employee, start_date, end_date)

		return sorted(set(date_range) - set(holiday_dates) - set(marked_attendance_dates))

	def get_start_and_end_dates(self, employee):
		"""Returns start and end dates for checking attendance and marking absent
		return: start date = max of `process_attendance_after` and DOJ
		return: end date = min of shift before `last_sync_of_checkin` and Relieving Date
		"""
		date_of_joining, relieving_date, employee_creation = frappe.get_cached_value(
			"Employee", employee, ["date_of_joining", "relieving_date", "creation"]
		)

		if not date_of_joining:
			date_of_joining = employee_creation.date()

		start_date = max(getdate(self.process_attendance_after), date_of_joining)
		end_date = None

		shift_details = get_shift_details(self.name, get_datetime(self.last_sync_of_checkin))
		last_shift_time = (
			shift_details.actual_end if shift_details else get_datetime(self.last_sync_of_checkin)
		)

		# check if shift is found for 1 day before the last sync of checkin
		# absentees are auto-marked 1 day after the shift to wait for any manual attendance records
		prev_shift = get_employee_shift(employee, last_shift_time - timedelta(days=1), True, "reverse")
		if prev_shift and prev_shift.shift_type.name == self.name:
			end_date = (
				min(prev_shift.start_datetime.date(), relieving_date)
				if relieving_date
				else prev_shift.start_datetime.date()
			)
		else:
			# no shift found
			return None, None
		return start_date, end_date

	def get_marked_attendance_dates_between(self, employee: str, start_date: str, end_date: str) -> list[str]:
		Attendance = frappe.qb.DocType("Attendance")
		return (
			frappe.qb.from_(Attendance)
			.select(Attendance.attendance_date)
			.where(
				(Attendance.employee == employee)
				& (Attendance.docstatus < 2)
				& (Attendance.attendance_date.between(start_date, end_date))
				& ((Attendance.shift.isnull()) | (Attendance.shift == self.name))
			)
		).run(pluck=True)

	def get_assigned_employees(self, from_date: datetime.date, consider_default_shift=False) -> list[str]:
		"""Get all such employees who either have this shift assigned that hasn't ended or have this shift as default shift.
		This may fetch some redundant employees who have another shift assigned that may have started or ended before or after the
		attendance processing date. But this is done to avoid missing any employee who may have this shift as active shift."""
		filters = {"shift_type": self.name, "docstatus": "1", "status": "Active"}

		or_filters = [["end_date", ">=", from_date], ["end_date", "is", "not set"]]

		assigned_employees = frappe.get_all(
			"Shift Assignment", filters=filters, or_filters=or_filters, pluck="employee"
		)

		if consider_default_shift:
			default_shift_employees = frappe.get_all(
				"Employee", filters={"default_shift": self.name, "status": "Active"}, pluck="name"
			)
			assigned_employees = set(assigned_employees + default_shift_employees)

		# exclude inactive employees
		inactive_employees = frappe.db.get_all("Employee", {"status": "Inactive"}, pluck="name")

		return list(set(assigned_employees) - set(inactive_employees))

	def get_holiday_list(self, employee: str) -> str:
		holiday_list_name = self.holiday_list or get_holiday_list_for_employee(employee, False)
		return holiday_list_name

	def should_mark_attendance(self, employee: str, attendance_date: str) -> bool:
		"""Determines whether attendance should be marked on holidays or not"""
		if self.mark_auto_attendance_on_holidays:
			# no need to check if date is a holiday or not
			# since attendance should be marked on all days
			return True

		holiday_list = self.get_holiday_list(employee)
		if is_holiday(holiday_list, attendance_date):
			return False
		return True


def update_last_sync_of_checkin():
	"""Called from hooks"""
	shifts = frappe.get_all(
		"Shift Type",
		filters={"enable_auto_attendance": 1, "auto_update_last_sync": 1},
		fields=["name", "last_sync_of_checkin", "start_time"],
	)
	now = frappe.flags.current_datetime or get_datetime()
	for shift in shifts:
		time_within_shift = datetime.combine(now.date(), get_time(shift.start_time))
		shift_end = get_shift_details(shift.name, time_within_shift)["actual_end"]
		update_last_sync = None
		if shift.last_sync_of_checkin:
			if get_datetime(shift.last_sync_of_checkin) < shift_end < now:
				update_last_sync = True
		elif shift_end < now:
			update_last_sync = True
		if update_last_sync:
			frappe.db.set_value(
				"Shift Type", shift.name, "last_sync_of_checkin", shift_end + timedelta(minutes=1)
			)


def process_auto_attendance_for_all_shifts():
	"""Called from hooks"""
	shift_list = frappe.get_all("Shift Type", filters={"enable_auto_attendance": "1"}, pluck="name")
	for shift in shift_list:
		print("Shift Type:", shift)
		doc = frappe.get_cached_doc("Shift Type", shift)
		doc.process_auto_attendance()


def get_time_difference(obj1, obj2):
	difference = (obj1 - obj2).time()
	hours, minutes, seconds_microseconds = str(difference).split(":")
	seconds, microseconds = seconds_microseconds.split(".")
	hours = int(hours)
	minutes = int(minutes)
	seconds = int(seconds)
	microseconds = int(microseconds)
	total_minutes = hours * 60 + minutes + seconds / 60 + microseconds / 60000000
	return round(total_minutes)


def get_assigned_employees_with_specified_threshold(
	name, from_date=None, start_time=None, end_time=None
) -> list[str]:
	filters = {"shift_type": name, "docstatus": "1", "status": "Active"}
	if from_date:
		filters["start_date"] = (">=", from_date)
	assigned_employees = frappe.get_all("Shift Assignment", filters=filters, pluck="employee")

	# exclude inactive employees
	inactive_employees = frappe.db.get_all("Employee", {"status": "inactive"}, pluck="name")

	if start_time is not None:
		assigned_employees = [
			emp
			for emp in assigned_employees
			if (
				start_time - 1
				<= int(frappe.get_value("Employee", emp, "custom_notification_threshold_checkin") or 15)
				<= start_time + 1
			)
			and not has_valid_log_for_today(in_log="IN", emp=emp)
		]
	elif end_time is not None:
		assigned_employees = [
			emp
			for emp in assigned_employees
			if (
				end_time - 1
				<= int(frappe.get_value("Employee", emp, "custom_notification_threshold_checkout") or 15)
				<= end_time + 1
			)
			and not has_valid_log_for_today(in_log="OUT", emp=emp)
		]
	return list(set(assigned_employees) - set(inactive_employees))


def has_valid_log_for_today(in_log=None, out_log=None, emp=None):
	today = datetime.today()
	log_type = in_log if in_log else out_log
	valid_log = frappe.db.sql(
		"""SELECT log_type FROM `tabEmployee Checkin`
		WHERE CAST(time as DATE)=%(time_val)s AND log_type=%(log_type)s AND employee = %(emp)s
		""",
		{"time_val": today.strftime("%Y-%m-%d"), "log_type": log_type, "emp": emp},
	)
	return True if valid_log else False


def notify_employees_to_checkin_or_checkout():
	frappe.utils.logger.set_log_level("DEBUG")
	notification_logger = frappe.logger("reminder_notifications", allow_site=True, file_count=10)
	notify_checkin = notify_checkout = []
	# Fetch reminder interval from HR Settings
	reminder_interval_minutes = (
		frappe.db.get_single_value("HR Settings", "follow_up_checkin_reminder_interval") or 30
	)
	reminder_interval_seconds = reminder_interval_minutes * 60
	now = frappe.utils.now_datetime()
	today = frappe.utils.getdate(now)
	two_hours_back = frappe.utils.add_to_date(now, hours=-2)
	query = """
			SELECT start_time, end_time, name, holiday_list
			FROM `tabShift Type`
			WHERE (start_time BETWEEN %s AND %s) OR (end_time BETWEEN %s AND %s)
		"""

	# Execute the query with the formatted time strings
	shifts = frappe.db.sql(query, (two_hours_back, now, two_hours_back, now), as_dict=True)
	for shift in shifts:
		if is_holiday(shift.holiday_list, today):
			continue
		notify_checkin = []
		notify_checkout = []
		time_difference_in = get_time_difference(now, shift.start_time)
		time_difference_out = get_time_difference(now, shift.end_time)
		# Get all employees for the shift. Reminder timing will be checked inside the loop.
		employees_assigned_to_shift = get_assigned_employees_with_specified_threshold(shift.name)

		for emp in employees_assigned_to_shift:
			# Skip if already checked-in for the day
			if has_valid_log_for_today(in_log="IN", emp=emp):
				continue

			employee = frappe.get_doc("Employee", emp)
			if not employee or not employee.user_id:
				continue

			# 1. Skip if on approved leave
			leave_status = get_employee_leave_status(employee.name, today)
			if leave_status in ["On Leave", "Half Day"]:
				continue

			reminder_count = _get_today_checkin_reminder_count(employee.user_id, today)
			custom_checkin_threshold = int(
				frappe.get_value("Employee", emp, "custom_notification_threshold_checkin") or 15
			)

			if reminder_count == 0:
				# First reminder: Send if current time is past the grace period.
				if time_difference_in >= custom_checkin_threshold:
					_send_checkin_reminder(employee)
					notify_checkin.append(employee.user_id)
			elif reminder_count < 3:
				# 2nd and 3rd reminder logic (30 mins after previous)
				last_reminder_time = _get_last_checkin_reminder_time(employee.user_id, today)
				if (
					last_reminder_time
					and (now - get_datetime(last_reminder_time)).total_seconds() >= reminder_interval_seconds
				):  # 30 minutes
					_send_checkin_reminder(employee)
					notify_checkin.append(employee.user_id)

					if reminder_count == 2:  # This was the 2nd reminder, making the current one the 3rd
						if not _check_clockify_activity(employee):
							_notify_hr_about_missed_checkin(employee)

		employees_closer_to_checkout = get_assigned_employees_with_specified_threshold(
			shift.name, end_time=time_difference_out
		)
		for emp in employees_closer_to_checkout:
			employee = frappe.get_doc("Employee", emp)
			if employee:
				notify_checkout.append(employee.user_id)
			frappe.enqueue(
				method="fcm_notification.send_notification.send_push_to_user",
				email=employee.user_id,
				title="Time to Check Out!",
				message="Your shift is almost over. Please remember to check out. Have a great evening!",
			)
		notification_logger.info(f"Employees to be notified for Check In: {notify_checkin}")
		notification_logger.info(f"Employees to be notified for Check Out: {notify_checkout}")


def _get_today_checkin_reminder_count(user_id, date):
	"""Counts the number of check-in reminders sent to a user for a specific day."""
	count = frappe.db.sql(
		"""
		SELECT count(*)
		FROM `tabHR Notifications` notif
		JOIN `tabNotification User` user ON notif.name = user.parent
		WHERE user.user = %s
		AND notif.type = 'Daily Check-in Reminder'
		AND DATE(notif.creation) = %s
	""",
		(user_id, date),
	)
	return count[0][0] if count else 0


def _get_last_checkin_reminder_time(user_id, date):
	"""Gets the timestamp of the last check-in reminder sent to a user for a specific day."""
	time = frappe.db.sql(
		"""
		SELECT notif.creation
		FROM `tabHR Notifications` notif
		JOIN `tabNotification User` user ON notif.name = user.parent
		WHERE user.user = %s
		AND notif.type = 'Daily Check-in Reminder'
		AND DATE(notif.creation) = %s
		ORDER BY notif.creation DESC
		LIMIT 1
	""",
		(user_id, date),
	)
	return time[0][0] if time else None


def _send_checkin_reminder(employee):
	"""Sends a check-in reminder push notification and logs it."""
	message = "Good morning! Please remember to check in for your shift. Have a productive day!"
	frappe.enqueue(
		method="fcm_notification.send_notification.send_push_to_user",
		email=employee.user_id,
		title="Don’t Forget to Check In!",
		message=message,
	)
	notification_doc = frappe.get_doc(
		{
			"doctype": "HR Notifications",
			"subject": f"Check-in Reminder Notification for Employee {employee.employee_name}",
			"message": message,
			"type": "Daily Check-in Reminder",
			"user": [{"user": employee.user_id}],
			"send_push": 0,
			"send_slack": 1,
		}
	)
	notification_doc.insert(ignore_permissions=True)

	# Update the doc to reflect that a push was sent, without triggering another send
	frappe.db.set_value("HR Notifications", notification_doc.name, "send_push", 1, update_modified=False)



def _check_clockify_activity(employee):
	"""
	Checks for active Clockify timers or any time entries for the employee for the current day.
	"""
	now = frappe.utils.now_datetime()
	today_start = now.replace(hour=0, minute=0, second=0)
	today_end = now.replace(hour=23, minute=59, second=59)

	custom_api_key, custom_user_id, workspace_ids, _, _ = get_employee_clockify_details(
		employee.name
	)

	if not (custom_api_key and custom_user_id and workspace_ids):
		# Cannot check credentials, assume no activity to allow HR notification to proceed.
		msg = f"Employee {employee.name} missing one or more custom Clockify credentials."
		frappe.log_error(message=msg, title="Daily Check-in Reminder")
		return False

	# Check for an active timer in any workspace
	for ws in workspace_ids:
		if is_clockify_timer_active(custom_api_key, ws, custom_user_id):
			return True  # Active timer found, so we skip notifying HR.
		# Check for any time entries for the entire day
		entries = get_clockify_time_entries(custom_api_key, ws, custom_user_id, today_start, today_end)
		if entries:
			return True  # Entries found, so we skip notifying HR.

	return False  # No activity found

def _get_last_checkin_reminder_name(user_id, date):
	"""Gets the name of the last check-in reminder doc sent to a user for a specific day."""
	doc_name = frappe.db.sql(
		"""
		SELECT notif.name
		FROM `tabHR Notifications` notif
		JOIN `tabNotification User` user ON notif.name = user.parent
		WHERE user.user = %s
		AND notif.type = 'Daily Check-in Reminder'
		AND DATE(notif.creation) = %s
		ORDER BY notif.creation DESC
		LIMIT 1
	""",
		(user_id, date),
	)
	return doc_name[0][0] if doc_name else None

def _notify_hr_about_missed_checkin(employee):
	"""Notifies HR about an employee who missed check-in after 3 reminders."""
	notification = frappe.get_doc("Notification", "Notify HR about Missed Checkins")
	message = _(
		"{0} has not checked in after 3 reminders and no Leave or Clockify activity was detected. Please follow up."
	).format(employee.employee_name)
	webhook_url= notification.slack_webhook_url
	last_reminder_name = _get_last_checkin_reminder_name(employee.user_id, frappe.utils.getdate(frappe.utils.now()))
	send_slack_message(webhook_url, message, "HR Notifications", last_reminder_name)
