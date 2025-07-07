# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc

from hrms.controllers.employee_boarding_controller import EmployeeBoardingController, get_onboarding_details


class IncompleteTaskError(frappe.ValidationError):
	pass


class EmployeeOnboarding(EmployeeBoardingController):
	def validate(self):
		super().validate()
		self.set_employee()
		self.validate_duplicate_employee_onboarding()

	def set_employee(self):
		if not self.employee:
			self.employee = frappe.db.get_value("Employee", {"job_applicant": self.job_applicant}, "name")

	def validate_duplicate_employee_onboarding(self):
		emp_onboarding = frappe.db.exists(
			"Employee Onboarding", {"job_applicant": self.job_applicant, "docstatus": ("!=", 2)}
		)
		if emp_onboarding and emp_onboarding != self.name:
			frappe.throw(
				_("Employee Onboarding: {0} already exists for Job Applicant: {1}").format(
					frappe.bold(emp_onboarding), frappe.bold(self.job_applicant)
				)
			)

	def validate_employee_creation(self):
		if self.docstatus != 1:
			frappe.throw(_("Submit this to create the Employee record"))
		else:
			for activity in self.activities:
				if not activity.required_for_employee_creation:
					continue
				else:
					task_status = frappe.db.get_value("Task", activity.task, "status")
					if task_status not in ["Completed", "Cancelled"]:
						frappe.throw(
							_("All the mandatory tasks for employee creation are not completed yet."),
							IncompleteTaskError,
						)

	def on_submit(self):
		super().on_submit()

	def on_update_after_submit(self):
		self.create_task_and_notify_user()

	# def on_update(self):
	# 	self.create_task_and_notify_user()
		
	def on_cancel(self):
		super().on_cancel()

	@frappe.whitelist()
	def mark_onboarding_as_completed(self):
		if not frappe.has_permission("Employee Onboarding", "write", self.name):
			raise frappe.PermissionError(_("You do not have permission to complete this Employee Onboarding."))
		for activity in self.activities:
			frappe.db.set_value("Task", activity.task, "status", "Completed")
		frappe.db.set_value("Project", self.project, "status", "Completed")
		self.boarding_status = "Completed"
		self.save()


@frappe.whitelist()
def make_employee(source_name, target_doc=None):
	doc = frappe.get_doc("Employee Onboarding", source_name)
	doc.validate_employee_creation()

	def set_missing_values(source, target):
		target.personal_email = frappe.db.get_value("Job Applicant", source.job_applicant, "email_id")
		target.status = "Active"

	doc = get_mapped_doc(
		"Employee Onboarding",
		source_name,
		{
			"Employee Onboarding": {
				"doctype": "Employee",
				"field_map": {
					"first_name": "employee_name",
					"employee_grade": "grade",
				},
			}
		},
		target_doc,
		set_missing_values,
	)
	return doc

@frappe.whitelist()
def check_employee_onboarding_exists(job_applicant):
	if not frappe.has_permission("Employee Onboarding", "read"):
		raise frappe.PermissionError(_("You do not have permission to check Employee Onboarding."))
	exists = frappe.db.exists("Employee Onboarding", {"job_applicant": job_applicant, "docstatus": ["!=", 2]})
	return {"exists": bool(exists)}

@frappe.whitelist()
def create_employee_onboarding_from_applicant(job_applicant, company, date_of_joining, holiday_list, employee_onboarding_template):
	if not frappe.has_permission("Employee Onboarding", "create"):
		raise frappe.PermissionError(_("You do not have permission to create Employee Onboarding."))
	doc = frappe.new_doc("Employee Onboarding")
	doc.job_applicant = job_applicant
	doc.company = company
	doc.date_of_joining = date_of_joining
	doc.boarding_begins_on = date_of_joining
	doc.holiday_list = holiday_list
	doc.employee_onboarding_template = employee_onboarding_template
	doc.notify_users_by_email = 1

	# Use the existing function to copy activities from the template
	if employee_onboarding_template:
		template_activities = get_onboarding_details(employee_onboarding_template, "Employee Onboarding Template")
		for activity in template_activities:
			doc.append("activities", activity)

	doc.insert(ignore_permissions=True)
	return doc.name
