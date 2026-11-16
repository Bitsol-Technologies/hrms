# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.desk.form import assign_to
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, format_datetime, getdate, unique

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
			project_name += self.job_applicant or self.employee_name
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
		holiday_list = self.get_holiday_list()

		# Bitsol: tasks are only created (and assignees e-mailed) when "Notify users by email" is ticked
		if not self.get("notify_users_by_email"):
			return

		# create the task for the given project and assign to the concerned person
		for activity in self.activities:
			if activity.task:
				continue

			dates = self.get_task_dates(activity, holiday_list)

			task = frappe.get_doc(
				{
					"doctype": "Task",
					"project": self.project,
					"subject": activity.activity_name + " : " + self.employee_name,
					"description": activity.description,
					"department": self.department,
					"company": self.company,
					"task_weight": activity.task_weight,
					"exp_start_date": dates[0],
					"exp_end_date": dates[1],
				}
			).insert(ignore_permissions=True)
			activity.db_set("task", task.name)

			users = [activity.user] if activity.user else []
			if activity.role:
				has_role = frappe.qb.DocType("Has Role")
				user = frappe.qb.DocType("User")
				user_list = (
					frappe.qb.from_(has_role)
					.left_join(user)
					.on(has_role.parent == user.name)
					.select(has_role.parent)
					.distinct()
					.where(
						(has_role.parenttype == "User")
						& (user.enabled == 1)
						& (has_role.role == activity.role)
					)
					.run(pluck="parent")
				)
				users = unique(users + user_list)

				if "Administrator" in users:
					users.remove("Administrator")

			# assign the task the users
			if users:
				self.assign_task_to_users(task, users)
				if self.doctype == "Employee Separation":
					send_boarding_activity_notification(
						users, activity, self.employee_name, "Employee", "Separation Task Notification"
					)
				else:
					send_boarding_activity_notification(
						users,
						activity,
						# v16: an onboarding can be created from a Job Offer that has no Job Applicant
						self.get("applicant_name") or self.employee_name,
						"Job Applicant",
						"Onboarding Task Notification",
					)

	def get_holiday_list(self):
		if self.doctype == "Employee Separation":
			return get_holiday_list_for_employee(self.employee, as_on=self.boarding_begins_on)
		else:
			if self.employee:
				return get_holiday_list_for_employee(self.employee, as_on=self.boarding_begins_on)
			else:
				if not self.holiday_list:
					frappe.throw(_("Please set the Holiday List."), frappe.MandatoryError)
				else:
					return self.holiday_list

	def get_task_dates(self, activity, holiday_list):
		start_date = end_date = None

		# Bitsol: "Begin On" is an explicit date-time on the activity (upstream: day offset from
		# `boarding_begins_on`). Task dates are plain dates, so only the date part is used.
		if activity.begin_on:
			start_date = self.update_if_holiday(getdate(activity.begin_on), holiday_list)

			# ensure duration is at least 1 day
			duration = cint(activity.duration) or 1

			end_date = add_days(start_date, duration)
			end_date = self.update_if_holiday(end_date, holiday_list)

		return [start_date, end_date]

	def update_if_holiday(self, date, holiday_list):
		while is_holiday(holiday_list, date):
			date = add_days(date, 1)
		return date

	def assign_task_to_users(self, task, users):
		for user in users:
			args = {
				"assign_to": [user],
				"doctype": task.doctype,
				"name": task.name,
				"description": task.description or task.subject,
				"notify": self.notify_users_by_email,
			}
			assign_to.add(args)

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


def get_enabled_users_with_role(role: str) -> list[str]:
	has_role = frappe.qb.DocType("Has Role")
	user = frappe.qb.DocType("User")
	users = (
		frappe.qb.from_(has_role)
		.left_join(user)
		.on(has_role.parent == user.name)
		.select(has_role.parent)
		.distinct()
		.where((has_role.parenttype == "User") & (user.enabled == 1) & (has_role.role == role))
		.run(pluck="parent")
	)
	return [d for d in users if d != "Administrator"]


def send_email_from_template(
	recipients: list[str],
	email_template_name: str,
	args: dict,
	fallback_subject: str,
	fallback_message: str,
	subject_prefix: str = "",
	**sendmail_kwargs,
):
	"""Send an e-mail rendered from the **Email Template** `email_template_name` (site data).

	The template is rendered here with the stock Email Template API so this does not depend on any
	extra `frappe.sendmail` keyword. If the template does not exist on the site, a plain built-in
	message is sent instead of failing the transaction.
	"""
	if frappe.db.exists("Email Template", email_template_name):
		email_template = frappe.get_cached_doc("Email Template", email_template_name)
		subject = email_template.get_formatted_subject(args)
		message = email_template.get_formatted_response(args)
	else:
		subject = fallback_subject
		message = fallback_message

	subject = f"{subject_prefix or ''}{subject}"
	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=message,
		header=subject,
		**sendmail_kwargs,
	)


def send_boarding_activity_notification(
	users, activity, entity_name, reference_doctype, email_template_name, subject_prefix=""
):
	"""
	Sends a task notification email for a given activity (onboarding or separation).

	Parameters:
	users: List of user emails to notify.
	activity: An Employee Boarding Activity row (document).
	entity_name: The employee or job applicant name shown in the mail.
	reference_doctype: The reference doctype ("Job Applicant" or "Employee").
	email_template_name: The Email Template to use.
	subject_prefix: Optional prefix for the email subject (e.g., "RE: " for updates).

	Nothing is sent if the row's `notification_sent` flag is already set; the flag is set after sending.
	"""
	users = unique([user for user in (users or []) if user])
	if not users or activity.get("notification_sent"):
		return

	begin_on = format_datetime(activity.get("begin_on")) if activity.get("begin_on") else ""
	for user in users:
		args = {
			"user_first_name": frappe.db.get_value("User", user, "first_name") or user,
			"entity_name": entity_name,
			"activity_name": activity.get("activity_name"),
			"description": activity.get("description"),
			"begin_on": activity.get("begin_on"),
		}
		send_email_from_template(
			[user],
			email_template_name,
			args,
			fallback_subject=_("Task assigned: {0} ({1})").format(activity.get("activity_name"), entity_name),
			fallback_message=_(
				"Hello {0},<br><br>The activity <b>{1}</b> for <b>{2}</b> has been assigned to you.<br>Begins on: {3}<br><br>{4}"
			).format(
				args["user_first_name"],
				activity.get("activity_name"),
				entity_name,
				begin_on,
				activity.get("description") or "",
			),
			subject_prefix=subject_prefix,
			reference_doctype=reference_doctype,
			now=True,
		)

	# Mark the activity as notified to avoid duplicate notifications
	activity.db_set("notification_sent", 1, update_modified=False)


@frappe.whitelist()
def get_onboarding_details(parent: str, parenttype: str):
	frappe.has_permission(parenttype, "read", parent, throw=True)
	# Bitsol: `role` is intentionally not copied from templates (activities are assigned to a named
	# assignee); `activity_type` is mandatory on the row, so it has to come along.
	return frappe.get_all(
		"Employee Boarding Activity",
		fields=[
			"activity_name",
			"activity_type",
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


@frappe.whitelist(methods=["POST"])
def reset_and_notify(child_name: str, parent: str, subject_prefix: str = "RE: "):
	"""Reset the notification flag of one onboarding activity and e-mail its assignees again."""
	frappe.has_permission("Employee Onboarding", "write", parent, throw=True)

	parent_doc = frappe.get_doc("Employee Onboarding", parent)
	for activity in parent_doc.activities:
		if activity.name == child_name:
			activity.db_set("notification_sent", 0, update_modified=False)

			users = [activity.user] if activity.user else []
			if activity.role:
				users = unique(users + get_enabled_users_with_role(activity.role))

			send_boarding_activity_notification(
				users,
				activity,
				parent_doc.get("applicant_name") or parent_doc.employee_name,
				"Job Applicant",
				"Onboarding Task Notification",
				subject_prefix,
			)
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
