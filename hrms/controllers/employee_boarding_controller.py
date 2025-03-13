# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.desk.form import assign_to
from frappe.model.document import Document
from frappe.utils import add_days, flt, unique

from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday


class EmployeeBoardingController(Document):
	"""
	Create the project and the task for the boarding process
	Assign to the concerned person as per the onboarding/separation template
	"""

	def validate(self):
		# remove the task if linked before submitting the form
		if self.amended_from:
			for activity in self.activities:
				activity.task = ""

	def on_submit(self):
		# create the project for the given employee onboarding
		project_name = _(self.doctype) + " : "
		if self.doctype == "Employee Onboarding":
			project_name += self.job_applicant
		else:
			project_name += self.employee

		project = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": project_name,
				"expected_start_date": self.date_of_joining
				if self.doctype == "Employee Onboarding"
				else self.resignation_letter_date,
				"department": self.department,
				"company": self.company,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		self.db_set("project", project.name)
		self.db_set("boarding_status", "Pending")
		self.reload()
		self.create_task_and_notify_user()

	def create_task_and_notify_user(self):
		# create the task for the given project and assign to the concerned person
		if not self.get("notify_users_by_email"):
			return
		
		for activity in self.activities:
			send_boarding_activity_notification(activity, self.applicant_name)

	def get_holiday_list(self):
		if self.doctype == "Employee Separation":
			return get_holiday_list_for_employee(self.employee)
		else:
			if self.employee:
				return get_holiday_list_for_employee(self.employee)
			else:
				if not self.holiday_list:
					frappe.throw(_("Please set the Holiday List."), frappe.MandatoryError)
				else:
					return self.holiday_list

	def get_task_dates(self, activity, holiday_list):
		start_date = end_date = None

		if activity.begin_on is not None:
			start_date = add_days(self.boarding_begins_on, activity.begin_on)
			start_date = self.update_if_holiday(start_date, holiday_list)

			if activity.duration is not None:
				end_date = add_days(self.boarding_begins_on, activity.begin_on + activity.duration)
				end_date = self.update_if_holiday(end_date, holiday_list)

		return [start_date, end_date]

	def update_if_holiday(self, date, holiday_list):
		while is_holiday(holiday_list, date):
			date = add_days(date, 1)
		return date

	def on_cancel(self):
		# delete task project
		project = self.project
		for task in frappe.get_all("Task", filters={"project": project}):
			frappe.delete_doc("Task", task.name, force=1)
		frappe.delete_doc("Project", project, force=1)
		self.db_set("project", "")
		for activity in self.activities:
			activity.db_set("task", "")

		frappe.msgprint(
			_("Linked Project {} and Tasks deleted.").format(project), alert=True, indicator="blue"
		)
from frappe.utils import format_datetime
def send_boarding_activity_notification(activity, job_applicant, subject_prefix=""):
	"""
	Sends an onboarding task notification email for a given activity.
	
	Parameters:
	activity: A dictionary or document object representing an Employee Boarding Activity row.
	job_applicant: The employee or job applicant identifier.
	subject_prefix: Optional prefix for the email subject (e.g., "RE: " for updates).
	
	The function checks if an user exists and if the notification_sent flag is false.
	After sending the email, it sets notification_sent to true.
	"""
	# Check if there's an user and notification hasn't been sent already
	if activity.get("user") and not activity.get("notification_sent"):
		user_doc = frappe.get_doc("User", activity.get("user"))
		user_first_name = user_doc.first_name or activity.get("user")
		
		# Format the date using Frappe's format_datetime
		formatted_date = format_datetime(activity.get("begin_on"))
		
		subject = "{}Onboarding Task Notification for Employee: {}".format(subject_prefix, job_applicant)
		message = (
			"Hello, {}<br><br>"
			"You have been assigned a new onboarding task for employee <strong>{}</strong>.<br>"
			"<b>Date:</b> {}<br>"
			"<b>Task:</b> {}<br>"
			"{},<br><br>"
			"Regards,<br>HR Team"
		).format(
			user_first_name,
			job_applicant,
			formatted_date,
			activity.get("activity_name"),
			activity.get("duration"),
			activity.get("description")
		)
			
		# Send the email using Frappe's sendmail function
		frappe.sendmail(
			recipients=[activity.get("user")],
			subject=subject,
			message=message,
		)
		
		# Mark the activity as notified to avoid duplicate notifications
		activity.notification_sent = 1  # Checkbox: 1 indicates True
		activity.db_update()

@frappe.whitelist()
def get_onboarding_details(parent, parenttype):
	return frappe.get_all(
		"Employee Boarding Activity",
		fields=[
			"activity_name",
			"user",
			"required_for_employee_creation",
			"description",
			"task_weight",
			"begin_on",
			"duration",
		],
		filters={"parent": parent, "parenttype": parenttype},
		order_by="idx",
	)

@frappe.whitelist()
def reset_and_notify(child_name, parent, subject_prefix=""):
	print("CALLING reset_and_notify")
	# Reset the notification flag and send the notification email
	parent_doc = frappe.get_doc("Employee Onboarding", parent)
	for activity in parent_doc.activities:
		if activity.name == child_name:
			# Reset notification flag
			activity.notification_sent = 0
			activity.db_update()
			# Now call the notification function with the optional subject prefix
			send_boarding_activity_notification(activity, parent_doc.job_applicant, subject_prefix)
			break


def update_employee_boarding_status(project, event=None):
	employee_onboarding = frappe.db.exists("Employee Onboarding", {"project": project.name})
	employee_separation = frappe.db.exists("Employee Separation", {"project": project.name})

	if not (employee_onboarding or employee_separation):
		return

	status = "Pending"
	if flt(project.percent_complete) > 0.0 and flt(project.percent_complete) < 100.0:
		status = "In Process"
	elif flt(project.percent_complete) == 100.0:
		status = "Completed"

	if employee_onboarding:
		frappe.db.set_value("Employee Onboarding", employee_onboarding, "boarding_status", status)
	elif employee_separation:
		frappe.db.set_value("Employee Separation", employee_separation, "boarding_status", status)


def update_task(task, event=None):
	if task.project and not task.flags.from_project:
		update_employee_boarding_status(frappe.get_cached_doc("Project", task.project))
