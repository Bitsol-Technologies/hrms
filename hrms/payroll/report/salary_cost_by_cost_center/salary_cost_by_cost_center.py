# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, flt, getdate, nowdate

from hrms.payroll.doctype.salary_slip.salary_slip import get_employee_hourly_rate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	end_date = getdate(filters.get("end_date") or nowdate())
	start_date = getdate(filters.get("start_date") or add_days(end_date, -30))

	columns = get_columns()
	data = []

	employee_filters = {"status": "Active"}
	if filters.get("employee"):
		employee_filters["name"] = filters.employee
	if filters.get("company"):
		employee_filters["company"] = filters.company

	employees = frappe.get_list(
		"Employee", filters=employee_filters, fields=["name", "employee_name"], order_by="name asc"
	)

	for emp in employees:
		hourly_rate = get_employee_hourly_rate(emp.name)
		total_hours = get_total_timesheet_hours(emp.name, start_date, end_date)
		if not total_hours:
			continue

		salary_slip_filters = {
			"employee": emp.name,
			"start_date": ["between", [start_date, end_date]],
			"docstatus": 1,
		}
		if filters.get("company"):
			salary_slip_filters["company"] = filters.company
		if not frappe.get_list("Salary Slip", filters=salary_slip_filters, fields=["name"], limit=1):
			continue

		# cost center allocation from the latest Salary Structure Assignment
		ssa = frappe.get_list(
			"Salary Structure Assignment",
			filters={"employee": emp.name, "from_date": ["<=", end_date], "docstatus": ["!=", 2]},
			fields=["name"],
			order_by="from_date desc",
			limit=1,
		)
		if not ssa:
			continue

		ssa_doc = frappe.get_doc("Salary Structure Assignment", ssa[0].name)
		earnings = flt(total_hours) * flt(hourly_rate)
		for cc in ssa_doc.payroll_cost_centers:
			if filters.get("cost_center") and cc.cost_center != filters.cost_center:
				continue

			division = frappe.db.get_value("Cost Center", cc.cost_center, "parent_cost_center") or ""
			data.append(
				{
					"employee": emp.name,
					"employee_name": emp.employee_name,
					"cost_center": cc.cost_center,
					"division": division,
					"total_hours": total_hours,
					"hourly_rate": hourly_rate,
					"earnings": earnings,
					"allocation_percentage": flt(cc.percentage),
					"allocated_cost": earnings * flt(cc.percentage) / 100,
				}
			)

	return columns, data


def get_total_timesheet_hours(employee, start_date, end_date):
	timesheet = frappe.qb.DocType("Timesheet")
	detail = frappe.qb.DocType("Timesheet Detail")
	result = (
		frappe.qb.from_(detail)
		.inner_join(timesheet)
		.on(timesheet.name == detail.parent)
		.select(Sum(detail.hours))
		.where(
			(timesheet.employee == employee)
			& (timesheet.start_date[start_date:end_date])
			& (timesheet.docstatus == 1)
		)
	).run()
	return flt(result[0][0]) if result else 0.0


def get_columns():
	return [
		{
			"fieldname": "employee",
			"label": _("Employee"),
			"fieldtype": "Link",
			"options": "Employee",
			"width": 150,
		},
		{"fieldname": "employee_name", "label": _("Employee Name"), "fieldtype": "Data", "width": 150},
		{
			"fieldname": "cost_center",
			"label": _("Cost Center"),
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 200,
		},
		{"fieldname": "division", "label": _("Division"), "fieldtype": "Data", "width": 150},
		{"fieldname": "total_hours", "label": _("Total Hours"), "fieldtype": "Float", "width": 100},
		{"fieldname": "hourly_rate", "label": _("Hourly Rate (KWD)"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "earnings", "label": _("Earnings (KWD)"), "fieldtype": "Currency", "width": 120},
		{
			"fieldname": "allocation_percentage",
			"label": _("Allocation %"),
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"fieldname": "allocated_cost",
			"label": _("Allocated Cost (KWD)"),
			"fieldtype": "Currency",
			"width": 150,
		},
	]
