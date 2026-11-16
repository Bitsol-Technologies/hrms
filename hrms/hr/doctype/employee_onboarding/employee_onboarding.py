# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

from hrms.controllers.employee_boarding_controller import EmployeeBoardingController, get_onboarding_details


class IncompleteTaskError(frappe.ValidationError):
	pass


class EmployeeOnboarding(EmployeeBoardingController):
	def validate(self):
		super().validate()
		self.set_employee()
		self.set_names_from_job_applicant()
		self.validate_duplicate_employee_onboarding()

	def set_employee(self):
		if self.employee:
			return
		# Bitsol: an onboarding can be started from a Job Applicant without a Job Offer
		if self.job_offer:
			self.employee = frappe.db.get_value("Employee", {"job_offer": self.job_offer}, "name")
		if not self.employee and self.job_applicant:
			self.employee = frappe.db.get_value("Employee", {"job_applicant": self.job_applicant}, "name")

	def set_names_from_job_applicant(self):
		# Bitsol: v16 fetches `employee_name` (mandatory) from the Job Offer; onboardings started from a
		# Job Applicant without a Job Offer take the applicant's name instead.
		if self.job_applicant and not (self.employee_name and self.get("applicant_name")):
			applicant_name = frappe.db.get_value("Job Applicant", self.job_applicant, "applicant_name")
			if not self.get("applicant_name"):
				self.applicant_name = applicant_name
			if not self.employee_name:
				self.employee_name = applicant_name

	def validate_duplicate_employee_onboarding(self):
		# v16 de-duplicates by Job Offer; Bitsol onboardings without a Job Offer are de-duplicated by
		# Job Applicant instead.
		if self.job_offer:
			fieldname, label = "job_offer", _("Job Offer")
		elif self.job_applicant:
			fieldname, label = "job_applicant", _("Job Applicant")
		else:
			return

		emp_onboarding = frappe.db.exists(
			"Employee Onboarding", {fieldname: self.get(fieldname), "docstatus": ("!=", 2)}
		)
		if emp_onboarding and emp_onboarding != self.name:
			frappe.throw(
				_("Employee Onboarding: {0} already exists for {1}: {2}").format(
					frappe.bold(emp_onboarding), label, frappe.bold(self.get(fieldname))
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

	def on_cancel(self):
		super().on_cancel()

	@frappe.whitelist(methods=["POST"])
	def mark_onboarding_as_completed(self):
		self.check_permission("write")
		# Bitsol: tasks only exist when "Notify users by email" was ticked
		for activity in self.activities:
			if activity.task:
				frappe.db.set_value("Task", activity.task, "status", "Completed")
		if self.project:
			frappe.db.set_value("Project", self.project, "status", "Completed")
		self.boarding_status = "Completed"
		self.save()


@frappe.whitelist()
def make_employee(source_name: str, target_doc: str | Document | None = None) -> Document:
	doc = frappe.get_doc("Employee Onboarding", source_name)
	doc.validate_employee_creation()

	def set_missing_values(source, target):
		# Bitsol: fall back to the Job Applicant when the onboarding has no Job Offer
		if source.job_offer:
			target.personal_email = frappe.db.get_value("Job Offer", source.job_offer, "applicant_email")
			target.job_offer = source.job_offer
		elif source.job_applicant:
			target.personal_email = frappe.db.get_value("Job Applicant", source.job_applicant, "email_id")
		if source.job_applicant:
			target.job_applicant = source.job_applicant
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
def check_employee_onboarding_exists(job_applicant: str) -> dict:
	frappe.has_permission("Employee Onboarding", "read", throw=True)
	exists = frappe.db.exists("Employee Onboarding", {"job_applicant": job_applicant, "docstatus": ["!=", 2]})
	return {"exists": bool(exists)}


@frappe.whitelist()
def create_employee_onboarding_from_applicant(
	job_applicant: str,
	company: str,
	date_of_joining: str,
	holiday_list: str | None = None,
	employee_onboarding_template: str | None = None,
) -> str:
	frappe.has_permission("Employee Onboarding", "create", throw=True)
	frappe.has_permission("Job Applicant", "read", job_applicant, throw=True)

	doc = frappe.new_doc("Employee Onboarding")
	doc.job_applicant = job_applicant
	doc.applicant_name = doc.employee_name = frappe.db.get_value("Job Applicant", job_applicant, "applicant_name")
	doc.company = company
	doc.date_of_joining = date_of_joining
	doc.boarding_begins_on = date_of_joining
	doc.holiday_list = holiday_list
	doc.employee_onboarding_template = employee_onboarding_template
	doc.notify_users_by_email = 1

	# copy the activities from the template (same as selecting the template on the form)
	if employee_onboarding_template:
		template_activities = get_onboarding_details(employee_onboarding_template, "Employee Onboarding Template")
		for activity in template_activities:
			doc.append("activities", activity)

	doc.insert(ignore_permissions=True)
	return doc.name
