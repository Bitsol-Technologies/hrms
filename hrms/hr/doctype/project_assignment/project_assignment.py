# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class ProjectAssignment(Document):
	def validate(self):
		self.validate_dates()
		self.validate_total_hours()

	def validate_dates(self):
		if not (self.start_date and self.end_date):
			return

		start_date = getdate(self.start_date)
		end_date = getdate(self.end_date)
		if start_date > end_date:
			frappe.throw(_("End Date cannot be before Start Date"))

		project = frappe.db.get_value(
			"Project", self.project, ["expected_start_date", "expected_end_date"], as_dict=True
		)
		if not project or not project.expected_start_date or not project.expected_end_date:
			frappe.throw(_("Please set Expected Start Date and Expected End Date for the Project"))
		if start_date < getdate(project.expected_start_date) or end_date > getdate(
			project.expected_end_date
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
			limit=1,
		):
			frappe.throw(_("Employee is already assigned to a project in this period"))

	def validate_total_hours(self):
		# overlap can only be computed when the assignment period is known
		if not (self.start_date and self.end_date):
			return

		assignments = frappe.get_all(
			"Project Assignment",
			filters={
				"employee": self.employee,
				"status": "Active",
				"start_date": ("<=", self.end_date),
				"end_date": (">=", self.start_date),
				"name": ("!=", self.name),
				"docstatus": 1,
			},
			fields=["allocated_hours_daily"],
		)
		total_hours = flt(self.allocated_hours_daily) + sum(
			flt(assignment.allocated_hours_daily) for assignment in assignments
		)
		if total_hours > 8:
			# Bitsol: overbooking is a warning, not a blocking error
			frappe.msgprint(
				_("Overbooked - Working hours cannot exceed 8 hours per day"), indicator="orange", alert=True
			)
