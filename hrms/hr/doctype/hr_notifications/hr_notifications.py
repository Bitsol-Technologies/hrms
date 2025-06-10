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
		frappe.sendmail(
			recipients=users,
			subject=self.subject,
			message=self.message,
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
				send_push_to_user(user, self.subject, self.push_message)
			self.add_comment("Comment", f"Push notification sent to {', '.join(users)}")
		except Exception as e:
			self.log_error("Failed to send Push notification", frappe.get_traceback())
