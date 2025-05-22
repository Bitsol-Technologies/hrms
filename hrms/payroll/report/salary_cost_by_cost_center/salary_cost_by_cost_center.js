// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Salary Cost by Cost Center"] = {
	"filters": [
		{
			fieldname: "start_date",
			label: "Start Date",
			fieldtype: "Date",
	
		},
		{
			fieldname: "end_date",
			label: "End Date",
			fieldtype: "Date",
		
		},
		{
			fieldname: "employee",
			label: "Employee",
			fieldtype: "Link",
			options: "Employee"
		},
		{
			fieldname: "cost_center",
			label: "Cost Center",
			fieldtype: "Link",
			options: "Cost Center"
		},
		{
			fieldname: "company",
			label: "Company",
			fieldtype: "Link",
			options: "Company",
		}
	]
};
