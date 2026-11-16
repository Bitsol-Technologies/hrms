# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, format_datetime, get_datetime, getdate, today

from hrms.controllers.employee_boarding_controller import send_email_from_template


class EmployeeBoardingActivity(Document):
	pass


def send_onboarding_reminder():
	"""Bitsol (daily scheduler): e-mail the assignee of every onboarding activity that begins today.

	Uses the **Email Template** "Onboarding Reminder" (site data) with the context keys
	`user_first_name`, `applicant_name`, `formatted_date`, `activity_name`, `description`.
	"""
	day_start = get_datetime(getdate(today()))
	next_day_start = get_datetime(add_days(day_start, 1))

	activity = frappe.qb.DocType("Employee Boarding Activity")
	onboarding = frappe.qb.DocType("Employee Onboarding")
	activities = (
		frappe.qb.from_(activity)
		.inner_join(onboarding)
		.on(activity.parent == onboarding.name)
		.select(
			activity.user,
			activity.activity_name,
			activity.begin_on,
			activity.description,
			onboarding.applicant_name,
			onboarding.employee_name,
		)
		# only real onboardings (not templates/separations) that are submitted and still open
		.where(
			(activity.parenttype == "Employee Onboarding")
			& (activity.begin_on >= day_start)
			& (activity.begin_on < next_day_start)
			& (onboarding.docstatus == 1)
			& (onboarding.boarding_status != "Completed")
			& (activity.user.isnotnull())
			& (activity.user != "")
		)
		.run(as_dict=True)
	)

	for row in activities:
		user_first_name = frappe.db.get_value("User", row.user, "first_name") or row.user
		applicant_name = row.applicant_name or row.employee_name
		formatted_date = format_datetime(row.begin_on)
		context = {
			"user_first_name": user_first_name,
			"applicant_name": applicant_name,
			"formatted_date": formatted_date,
			"activity_name": row.activity_name,
			"description": row.description,
		}
		send_email_from_template(
			[row.user],
			"Onboarding Reminder",
			context,
			fallback_subject=_("Reminder: {0} ({1}) begins today").format(row.activity_name, applicant_name),
			fallback_message=_(
				"Hello {0},<br><br>This is a reminder that the onboarding activity <b>{1}</b> for <b>{2}</b> begins on {3}.<br><br>{4}"
			).format(user_first_name, row.activity_name, applicant_name, formatted_date, row.description or ""),
			reference_doctype="Employee Onboarding",
		)
