# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from hrms.hr.doctype.employee_checkin.employee_checkin import (
	send_slack_message_for_employee,
	split_long_message,
)
from fcm_notification.send_notification import send_push_to_user
from erpnext.setup.doctype.employee.employee import get_children
from collections import deque


class HRNotifications(Document):
	def validate(self):
		if not self.send_email and not self.send_slack and not self.send_push:
			frappe.throw(_("At least one notification method (Email, Slack, or Push) must be selected."))

	def after_insert(self):
		users = [d.user for d in self.user]
		if not users:
			return

		if self.send_email:
			self.send_email_notifications(users)

		if self.send_slack:
			self.send_slack_notifications(users)

		if self.send_push:
			self.send_push_notifications(users)

	def send_email_notifications(self, users):
		# Explicitly convert newline characters to HTML <br> tags for email clients
		email_message = self.message.replace("\n", "<br>")
		frappe.sendmail(
			recipients=users,
			subject=self.subject,
			message=email_message,
			now=True
		)
		self.add_comment("Comment", f"Email notification sent to {', '.join(users)}")

	def send_slack_notifications(self, users):
		try:
			messages = split_long_message(self.message)
			for message in messages:
				send_slack_message_for_employee(users, message)
			self.add_comment("Comment", f"Slack notification sent to {', '.join(users)}")
		except Exception as e:
			self.log_error("Failed to send Slack notification")

	def send_push_notifications(self, users):
		try:
			for user in users:
				send_push_to_user(user, self.subject, self.message)
			self.add_comment("Comment", f"Push notification sent to {', '.join(users)}")
		except Exception as e:
			self.log_error("Failed to send Push notification", frappe.get_traceback())

def get_all_reports(employee_id, company):
	"""
	Recursively fetches all employees reporting to the given employee_id
	by repeatedly calling the get_children function.
	"""
	all_reports_set = set()
	reports_to_process = deque([employee_id])
	processed_employees = set()

	while reports_to_process:
		current_manager_id = reports_to_process.popleft()
		if current_manager_id in processed_employees:
			continue
			
		processed_employees.add(current_manager_id)

		# Call get_children
		children = get_children(doctype="Employee", parent=current_manager_id, company=company)

		for child in children:
			child_employee_id = child.get("value")
			if child_employee_id and child_employee_id != current_manager_id:
				all_reports_set.add(child_employee_id)
				# If the child is expandable, they are also a manager, so process them
				if child.get("expandable"):
					reports_to_process.append(child_employee_id)
	return list(all_reports_set)


@frappe.whitelist()
def get_team_users(user):
	# List of roles that should bypass filtering
	privileged_roles = ["HR Manager"]

	# Skip filtering for Administrator or any user with privileged roles
	if user == "Administrator" or frappe.db.exists(
		"Has Role", {"parent": user, "role": ["in", privileged_roles]}
	):
		return {"restricted": False, "users": []}

	# Get the employee and their company, linked to the current user
	employee_data = frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, ["name", "company"], as_dict=True
	)
	if not employee_data:
		# If the user is not an employee, they cannot select anyone.
		return {"restricted": True, "users": []}

	employee_id = employee_data.name
	company = employee_data.company
	# Get all employees reporting to the current user's employee
	report_ids = get_all_reports(employee_id, company)
	# Get user_ids for the collected employees
	user_ids = frappe.get_all(
		"Employee", filters={"name": ("in", report_ids)}, fields=["user_id"], pluck="user_id"
	)

	final_user_list = {uid for uid in user_ids if uid}
	return {"restricted": True, "users": list(final_user_list)}
