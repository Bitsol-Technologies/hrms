# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ProjectAssignment(Document):
	def validate(self):
		self.validate_dates()
		self.validate_total_hours()

	def validate_dates(self):
		if self.start_date and self.end_date:
			if self.start_date > self.end_date:
				frappe.throw(_("End Date cannot be before Start Date"))
			project = frappe.get_doc(
				"Project", self.project, fields=["expected_start_date", "expected_end_date"]
			)
			frappe.db.exists("Project", self.project)
			if not project.expected_start_date or not project.expected_end_date:
				frappe.throw(_("Please set Expected Start Date and Expected End Date for the Project"))
			if (
				frappe.utils.data.getdate(self.start_date) < project.expected_start_date
				or frappe.utils.data.getdate(self.end_date) > project.expected_end_date
			):
				frappe.throw(_("Assignment dates should be within Project dates"))
			if frappe.get_all(
				"Project Assignment",
				filters={
					"employee": self.employee,
					"project": self.project,
					"status": "Active",
					"start_date": ("<=", self.end_date),
					"end_date": (">=", self.start_date),
					"name": ("!=", self.name),
					"docstatus": 1,
				},
			):
				frappe.throw(_("Employee is already assigned to a project in this period"))

	def validate_total_hours(self):
		projects = frappe.get_all(
			"Project Assignment",
			filters={
				"employee": self.employee,
				"status": "Active",
				"start_date": ("<=", self.end_date),
				"end_date": (">=", self.start_date),
				"name": ("!=", self.name),
				"docstatus": 1,
			},
			fields=["allocated_hours_daily", "allocated_hours_monthly"],
		)
		total_hours = self.allocated_hours_daily
		for project in projects:
			total_hours += project.allocated_hours_daily
		if total_hours > 8:
			frappe.msgprint(
				_("Overbooked - Working hours cannot exceed 8 hours per day"), indicator="orange", alert=True
			)
