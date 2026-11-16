# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import requests

import frappe
from frappe.model.document import Document

from hrms.hr.doctype.job_applicant.job_applicant import (
	SLACK_POST_MESSAGE_URL,
	SLACK_TIMEOUT,
	_get_slack_user_id,
)


class ApplicantPipeline(Document):
	"""First iteration of the Bitsol hiring pipeline (superseded by Job Applicant.applicant_status).

	Kept so that existing records stay readable and editable.
	"""

	def before_save(self):
		if not self.applicant_name:
			return

		applicant = (
			frappe.db.get_value(
				"Job Applicant",
				self.applicant_name,
				["applicant_name", "resume_link", "email_id", "job_title"],
				as_dict=True,
			)
			or frappe._dict()
		)
		job_opening = self.job_id or applicant.job_title
		job_opening_doc = (
			frappe.get_doc("Job Opening", job_opening)
			if job_opening and frappe.db.exists("Job Opening", job_opening)
			else None
		)

		# v15 compared against "Lead Review", which is not a Status option (dead branch);
		# "Lead Screening" is the matching option. Only notify when the status changes.
		status_changed = self.is_new() or self.has_value_changed("status")
		if status_changed and self.status in ("Lead Review", "Lead Screening") and job_opening_doc:
			leads_email = [row.email for row in job_opening_doc.get("cv_reviewers", []) if row.email]
			send_slack_message(leads_email, applicant.applicant_name, applicant.resume_link)

		if status_changed and self.status == "Rejected" and applicant.email_id:
			try:
				frappe.sendmail(
					recipients=[applicant.email_id],
					create_notification_log=True,
					from_users=["Administrator"],
					for_users=["Administrator"],
					args={
						"name": applicant.applicant_name,
						"title": job_opening_doc.get("job_title") if job_opening_doc else None,
					},
					email_template_name="Rejection Email",
				)
			except Exception:
				frappe.log_error(title="Applicant Pipeline Rejection Email Error")


def send_slack_message(emails: list[str], applicant_name: str | None, resume_link: str | None):
	"""Send an individual Slack DM to every e-mail address. Never raises."""
	if not emails:
		return

	# read lazily: System Settings is not available at import time
	system_settings = frappe.get_cached_doc("System Settings")
	slack_token = system_settings.get("slack_token")
	if not slack_token:
		return

	post_message_url = system_settings.get("slack_post_message_url") or SLACK_POST_MESSAGE_URL
	headers = {"Authorization": f"Bearer {slack_token}", "Content-Type": "application/json"}

	for email in emails:
		user_id = _get_slack_user_id(email)
		if not user_id:
			continue

		message = f"Hello <@{user_id}>, please review the CV of {applicant_name}.\nResume Link: {resume_link}"
		try:
			requests.post(
				post_message_url,
				headers=headers,
				data=json.dumps({"channel": user_id, "text": message}),
				timeout=SLACK_TIMEOUT,
			)
		except Exception:
			frappe.log_error(title="Slack message failed")
