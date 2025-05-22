# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
	columns, data = [], []
	# Default filters
	if not filters:
		filters = {}
	start_date = filters.get("start_date")
	if not start_date:
		start_date = frappe.utils.add_days(frappe.utils.nowdate(),-30)
	end_date = filters.get("end_date")
	if not end_date:
		end_date= frappe.utils.nowdate()
	employee = filters.get("employee")
	cost_center = filters.get("cost_center")

	# Columns definition
	columns = [
		{"fieldname": "employee", "label": "Employee", "fieldtype": "Link", "options": "Employee", "width": 150},
		{"fieldname": "employee_name", "label": "Employee Name", "fieldtype": "Data", "width": 150},
		{"fieldname": "cost_center", "label": "Cost Center", "fieldtype": "Link", "options": "Cost Center", "width": 200},
		{"fieldname": "division", "label": "Division", "fieldtype": "Data", "width": 150},
		{"fieldname": "total_hours", "label": "Total Hours", "fieldtype": "Float", "width": 100},
		{"fieldname": "hourly_rate", "label": "Hourly Rate (KWD)", "fieldtype": "Currency", "width": 120},
		{"fieldname": "earnings", "label": "Earnings (KWD)", "fieldtype": "Currency", "width": 120},
		{"fieldname": "allocation_percentage", "label": "Allocation %", "fieldtype": "Float", "width": 120},
		{"fieldname": "allocated_cost", "label": "Allocated Cost (KWD)", "fieldtype": "Currency", "width": 150}
	]

	# Data fetch and processing
	data = []
	employees = frappe.get_list("Employee", filters={"status": "Active"}, fields=["name", "employee_name"])
	for emp in employees:
		if employee and emp.name != employee:
			continue
		# Fetch custom hourly rate
		hourly_rate = frappe.db.get_value("Employee", emp.name, "hourly_rate") or 0
		# Fetch total hours from timesheets
		total_hours = frappe.db.sql("""
			SELECT SUM(`tabTimesheet Detail`.hours)
			FROM `tabTimesheet Detail`
			INNER JOIN `tabTimesheet` ON `tabTimesheet`.name = `tabTimesheet Detail`.parent
			WHERE `tabTimesheet`.employee = %s
			AND `tabTimesheet`.start_date BETWEEN %s AND %s
			AND `tabTimesheet`.docstatus = 1
		""", (emp.name, start_date, end_date), as_list=True)[0][0] or 0
		# Fetch salary slips
		salary_slips = frappe.get_list("Salary Slip", filters={
			"employee": emp.name,
			"start_date": ["between", [start_date, end_date]],
			"docstatus": 1
		}, fields=["name"])
		if not salary_slips or not total_hours:
			continue

		# Fetch cost center allocations from Salary Structure Assignment
		ssa = frappe.get_list("Salary Structure Assignment", filters={
			"employee": emp.name,
			"from_date": ["<=", end_date],
			"docstatus": ["!=", 2]
		}, fields=["name"], order_by="from_date desc", limit=1)
		if ssa:
			ssa_doc = frappe.get_doc("Salary Structure Assignment", ssa[0].name)
			for cc in ssa_doc.payroll_cost_centers:
				if cost_center and cc.cost_center != cost_center:
					continue
				division = frappe.db.get_value("Cost Center", cc.cost_center, "parent_cost_center") or ""
				earnings = total_hours * hourly_rate
				allocated_cost = (earnings * cc.percentage) / 100

				data.append({
					"employee": emp.name,
					"employee_name": emp.employee_name,
					"cost_center": cc.cost_center,
					"division": division,
					"total_hours": total_hours,
					"hourly_rate": hourly_rate,
					"earnings": earnings,
					"allocation_percentage": cc.percentage,
					"allocated_cost": allocated_cost
				})
	return columns, data
