// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["HR Notifications Summary"] = {
	"filters": [
		{
			"fieldname": "start_date",
			"label": "Start Date",
			"fieldtype": "Datetime",
			"default": frappe.datetime.add_days(frappe.datetime.get_today(), -30)
		},
		{
			"fieldname": "end_date",
			"label": "End Date",
			"fieldtype": "Datetime",
			"default": frappe.datetime.get_today()
		},
		{
			"fieldname": "employee",
			"label": "Employee",
			"fieldtype": "Link",
			"options": "Employee"
		}
	]
};

