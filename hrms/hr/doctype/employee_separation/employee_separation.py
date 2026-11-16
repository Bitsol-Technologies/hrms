# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from hrms.controllers.employee_boarding_controller import EmployeeBoardingController, get_onboarding_details


class EmployeeSeparation(EmployeeBoardingController):
	def validate(self):
		super().validate()
		self.validate_duplicate_employee_separation()

	def validate_duplicate_employee_separation(self):
		separation = frappe.db.exists(
			"Employee Separation", {"employee": self.employee, "docstatus": ("!=", 2)}
		)
		if separation and separation != self.name:
			frappe.throw(
				_("Employee Separation: {0} already exists for Employee: {1}").format(
					frappe.bold(separation), frappe.bold(self.employee)
				)
			)

	def on_submit(self):
		super().on_submit()

	def on_update_after_submit(self):
		self.create_task_and_notify_user()

	def on_cancel(self):
		super().on_cancel()

	@frappe.whitelist(methods=["POST"])
	def mark_separation_as_completed(self):
		# Bitsol: same behaviour as upstream's Employee Onboarding "Mark as Completed"
		self.check_permission("write")
		for activity in self.activities:
			if activity.task:
				frappe.db.set_value("Task", activity.task, "status", "Completed")
		if self.project:
			frappe.db.set_value("Project", self.project, "status", "Completed")
		self.boarding_status = "Completed"
		self.save()


@frappe.whitelist()
def create_employee_separation_from_employee(
	employee: str,
	company: str,
	relieving_date: str,
	employee_separation_template: str | None = None,
) -> str:
	frappe.has_permission("Employee Separation", "create", throw=True)
	frappe.has_permission("Employee", "read", employee, throw=True)

	doc = frappe.new_doc("Employee Separation")
	doc.employee = employee
	doc.company = company
	doc.boarding_begins_on = relieving_date
	doc.employee_separation_template = employee_separation_template
	doc.notify_users_by_email = 1

	# copy the activities from the template (same as selecting the template on the form)
	if employee_separation_template:
		template_activities = get_onboarding_details(employee_separation_template, "Employee Separation Template")
		for activity in template_activities:
			doc.append("activities", activity)

	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def check_employee_separation_exists(employee: str) -> dict:
	frappe.has_permission("Employee Separation", "read", throw=True)
	exists = frappe.db.exists("Employee Separation", {"employee": employee, "docstatus": ["!=", 2]})
	return {"exists": bool(exists)}
