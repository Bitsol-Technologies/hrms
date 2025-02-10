from datetime import datetime

import frappe
from frappe import _
from frappe.utils import add_days, get_first_day, get_last_day, getdate, nowdate


def is_holiday(holiday_list, date):
	if not holiday_list:
		return False

	date = getdate(date)
	holidays = frappe.get_all(
		"Holiday", filters={"parent": holiday_list, "holiday_date": date}, fields=["holiday_date"]
	)

	return bool(holidays)


@frappe.whitelist()
def get_current_month_working_days(company, start_date, end_date):
	user = frappe.session.user
	employee = frappe.get_value("Employee", {"user_id": user}, "name")

	if not employee:
		frappe.throw("Employee record not found for the logged-in user")

	start_date = getdate(start_date)
	end_date = getdate(end_date)
	holiday_list = frappe.get_value("Company", company, "default_holiday_list")

	total_working_days_count = 0
	current_date = start_date

	while current_date <= end_date:
		if (
			not is_holiday(holiday_list, current_date) and current_date.weekday() < 5
		):  # Count weekdays and non-holidays as working days
			total_working_days_count += 1
		current_date = add_days(current_date, 1)

	leaves = frappe.db.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"status": ["in", ["On Leave", "Half Day"]],
			"attendance_date": ["between", [start_date, end_date]],
			"docstatus": 1,
		},
		fields=["name", "employee", "status", "attendance_date"],
	)
	leave_days_count = 0
	for leave in leaves:
		if leave["status"] == "Half Day":
			leave_days_count += 0.5
		else:
			leave_days_count += 1

	working_days_till_today = 0
	current_date = start_date

	while current_date <= end_date:
		if (
			not is_holiday(holiday_list, current_date) and current_date.weekday() < 5
		):  # Count weekdays and non-holidays as working days
			working_days_till_today += 1
		current_date = add_days(current_date, 1)

	actual_working_days_count = working_days_till_today - leave_days_count
	return {
		"total_working_days": total_working_days_count,
		"off_days": leave_days_count,
		"employee_working_days": actual_working_days_count,
	}


def get_holiday_dates_between(
	holiday_list: str,
	start_date: str,
	end_date: str,
	skip_weekly_offs: bool = False,
) -> list:
	Holiday = frappe.qb.DocType("Holiday")
	query = (
		frappe.qb.from_(Holiday)
		.select(Holiday.holiday_date)
		.where((Holiday.parent == holiday_list) & (Holiday.holiday_date.between(start_date, end_date)))
		.orderby(Holiday.holiday_date)
	)

	if skip_weekly_offs:
		query = query.where(Holiday.weekly_off == 0)

	return query.run(pluck=True)


def invalidate_cache(doc, method=None):
	from hrms.payroll.doctype.salary_slip.salary_slip import HOLIDAYS_BETWEEN_DATES

	frappe.cache().delete_value(HOLIDAYS_BETWEEN_DATES)


@frappe.whitelist()
def get_leave_summary(start_date, end_date):
	# Get the current logged-in user
	current_user = frappe.session.user

	# Get the employee record linked to the logged-in user
	employee = frappe.get_value("Employee", {"user_id": current_user}, "name")

	if not employee:
		frappe.throw(_("No Employee record found for the current user."))

	# Calculate the total allocated leaves for the employee within the given date range
	total_allocated_leaves = (
		frappe.db.sql(
			"""
		SELECT SUM(total_leaves_allocated)
		FROM `tabLeave Allocation`
		WHERE employee = %s AND from_date >= %s AND to_date <= %s AND docstatus = 1
	""",
			(employee, start_date, end_date),
		)[0][0]
		or 0
	)

	# Calculate the total availed leaves (approved leave applications) within the given date range
	availed_leaves = (
		frappe.db.sql(
			"""
		SELECT SUM(total_leave_days)
		FROM `tabLeave Application`
		WHERE employee = %s AND status = 'Approved' AND from_date >= %s AND to_date <= %s AND docstatus = 1
	""",
			(employee, start_date, end_date),
		)[0][0]
		or 0
	)

	# Calculate the remaining balance of leaves
	remaining_leaves = total_allocated_leaves - availed_leaves

	# Return the result as a JSON response
	return {
		"total_allocated_leaves": total_allocated_leaves,
		"availed_leaves": availed_leaves,
		"remaining_leaves": remaining_leaves,
	}


@frappe.whitelist()
def calculate_expected_hours(start_date=None, end_date=None):
	# Get the current user
	current_user = frappe.session.user

	# Fetch the Employee record associated with the user
	employee = frappe.db.get_value("Employee", {"user_id": current_user}, "name")
	if not employee:
		return {"error": "No employee record found for the current user."}

	# Default to the current year's date range if not provided
	if not start_date or not end_date:
		current_year = datetime.now().year
		start_date = f"{current_year}-01-01"
		end_date = f"{current_year}-12-31"

	# Fetch the sum of expected_working_hours for the current user in the given date range
	total_hours = (
		frappe.db.sql(
			"""
		SELECT SUM(expected_working_hours) AS total_hours
		FROM `tabAttendance`
		WHERE docstatus = 1 AND attendance_date BETWEEN %s AND %s
		AND employee = %s
	""",
			(start_date, end_date, employee),
			as_dict=True,
		)[0].total_hours
		or 0
	)

	return {
		"employee": employee,
		"start_date": start_date,
		"end_date": end_date,
		"total_expected_working_hours": total_hours,
	}
