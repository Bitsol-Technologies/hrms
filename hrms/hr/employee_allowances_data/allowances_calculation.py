#!/usr/bin/python
import calendar
from datetime import timedelta

import frappe
from frappe.utils import date_diff, flt, getdate, today

DEFAULT_HOLIDAY_COMPANY = "Bitsol Technologies"
EMPLOYEE_FUEL_FIELDS = ("custom_distancekm", "custom_transport_type")
EMPLOYEE_MEDICAL_FIELDS = ("medical_allowance", "medical_availed", "medical_balance")
MIN_FUEL_ALLOWANCE = 1000
MAX_FUEL_ALLOWANCE = 10000


def employee_has_fields(fieldnames):
	meta = frappe.get_meta("Employee")
	return all(meta.has_field(fieldname) for fieldname in fieldnames)


def calculate_employee_fuel_allowance():
	# custom_distancekm / custom_transport_type are site-level Custom Fields on Employee;
	# skip quietly on sites that do not have them instead of failing the monthly job.
	if not employee_has_fields(EMPLOYEE_FUEL_FIELDS):
		return

	currentDate = getdate(today())

	# Calculate the start and end of the previous month
	firstDayOfCurrentMonth = currentDate.replace(day=1)
	endOfPreviousMonth = firstDayOfCurrentMonth - timedelta(days=1)
	startOfPreviousMonth = endOfPreviousMonth.replace(day=1)

	average_fuel_allowance = get_average_fuel_allowance_for_month(startOfPreviousMonth, endOfPreviousMonth)
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "custom_distancekm": ["!=", ""], "custom_transport_type": ["!=", ""]},
		fields=["name", "employee_name", "custom_distancekm", "custom_transport_type"],
	)

	total_days_in_month = calendar.monthrange(endOfPreviousMonth.year, endOfPreviousMonth.month)[1]
	public_holidays_count = fetch_public_holidays(startOfPreviousMonth, endOfPreviousMonth)
	total_days = total_days_in_month - public_holidays_count
	for emp in employees:
		if frappe.db.exists(
			"Employee Fuel Allowance", {"employee": emp.name, "fuel_allowance_month": endOfPreviousMonth}
		):
			# already generated for this month (job re-run)
			continue

		leaves = count_leaves(emp.name, startOfPreviousMonth, endOfPreviousMonth)
		wfh_days = count_work_from_home(emp.name, startOfPreviousMonth, endOfPreviousMonth)
		working_days = max(total_days - leaves - wfh_days, 0)
		fuel_allowance = calculate_allowance(emp, working_days, average_fuel_allowance)
		create_employee_fuel_allowance_record(emp, fuel_allowance, endOfPreviousMonth)


def days_within(from_date, to_date, start_date, end_date):
	"""Number of days of [from_date, to_date] that fall inside [start_date, end_date]."""
	from_date = max(getdate(from_date), getdate(start_date))
	to_date = min(getdate(to_date), getdate(end_date))
	if to_date < from_date:
		return 0
	return date_diff(to_date, from_date) + 1


def count_leaves(employee, start_date, end_date):
	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"from_date": ["<=", end_date],
			"to_date": [">=", start_date],
			"status": "Approved",
			"docstatus": 1,
		},
		fields=["name", "from_date", "to_date", "half_day"],
	)

	leave_days = 0
	for leave in leaves:
		# Skip the leave if it's a half-day
		if not leave.half_day:
			leave_days += days_within(leave.from_date, leave.to_date, start_date, end_date)

	return leave_days


def count_work_from_home(employee, start_date, end_date):
	if not frappe.db.exists("DocType", "Work From Home"):
		return 0

	wfh_applications = frappe.get_all(
		"Work From Home",
		filters={
			"employee": employee,
			"from_date": ["<=", end_date],
			"to_date": [">=", start_date],
			"status": "Approved",
			"docstatus": 1,
		},
		fields=["name", "from_date", "to_date"],
	)

	wfh_days = 0
	for wfh in wfh_applications:
		wfh_days += days_within(wfh.from_date, wfh.to_date, start_date, end_date)
	return wfh_days


def get_average_fuel_allowance_for_month(start_date, end_date):
	fuel_allowances = frappe.get_all(
		"Fuel Allowance",
		filters={"date": ["between", (start_date, end_date)]},
		fields=["fuel_amount", "base_fuel_amount"],
	)

	if not fuel_allowances:
		return 0
	# Calculate the difference between fuel_amount and base_fuel_amount for each record
	total_difference = sum(
		flt(fuel["fuel_amount"]) - flt(fuel["base_fuel_amount"]) for fuel in fuel_allowances
	)

	# Calculate the average of these differences
	average_difference = total_difference / len(fuel_allowances)
	return average_difference


def calculate_allowance(employee, working_days, fuel_allowance):
	if not fuel_allowance:
		return 0

	working_days = int(working_days)
	fuel_allowance = flt(fuel_allowance)
	# values come from the get_all row (v15 re-read them with the row dict as docname)
	distance = flt(employee.get("custom_distancekm"))
	transport_type = employee.get("custom_transport_type")
	vehicleMileage = 10
	if transport_type == "Bike":
		vehicleMileage = 20
	allowance = distance * 2 * working_days / vehicleMileage * fuel_allowance

	return allowance


def get_holiday_company():
	if frappe.db.exists("Company", DEFAULT_HOLIDAY_COMPANY):
		return DEFAULT_HOLIDAY_COMPANY
	return frappe.defaults.get_global_default("company")


def fetch_public_holidays(startDate, endDate):
	# Retrieve the default holiday list for the company (Bitsol Technologies, else the default company)
	company = get_holiday_company()
	if not company:
		return 0

	default_holiday_list_name = frappe.get_value("Company", company, "default_holiday_list")

	if not default_holiday_list_name:
		return 0

	# Fetch public holidays within the specified date range from the default holiday list
	public_holidays = frappe.get_all(
		"Holiday",
		filters={
			"parent": default_holiday_list_name,
			"parenttype": "Holiday List",
			"holiday_date": ["between", [startDate, endDate]],
		},
		fields=["holiday_date"],
	)
	return len(public_holidays)


def create_employee_fuel_allowance_record(employee, fuel_allowance, fuel_allowance_month):
	fuel_allowance = min(max(flt(fuel_allowance), MIN_FUEL_ALLOWANCE), MAX_FUEL_ALLOWANCE)

	new_record = frappe.get_doc(
		{
			"doctype": "Employee Fuel Allowance",
			"employee": employee["name"],
			"allowance_amount": fuel_allowance,
			"fuel_allowance_month": fuel_allowance_month,
		}
	)
	new_record.insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: scheduled job, keep each employee's record even if a later one fails


def reset_medical_allowances():
	if not employee_has_fields(EMPLOYEE_MEDICAL_FIELDS):
		return

	employees = frappe.get_all("Employee", fields=["name", "medical_allowance"])

	for employee in employees:
		# Set medical_availed to 0 and medical_balance to medical_allowance
		frappe.db.set_value(
			"Employee",
			employee["name"],
			{"medical_availed": 0, "medical_balance": flt(employee["medical_allowance"])},
			update_modified=False,
		)
	frappe.db.commit()  # nosemgrep: scheduled job
