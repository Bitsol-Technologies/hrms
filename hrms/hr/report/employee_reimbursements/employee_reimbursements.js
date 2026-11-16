// your_app/report/employee_reimbursements/employee_reimbursements.js

frappe.query_reports["Employee Reimbursements"] = {
	filters: [
	  {
		fieldname: "from_date",
		label: __("From Date"),
		fieldtype: "Date",
	  },
	  {
		fieldname: "to_date",
		label: __("To Date"),
		fieldtype: "Date",
	  }
	]
  };
  