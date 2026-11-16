# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# For license information, please see license.txt


import json

import requests

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.model.naming import append_number_if_name_exists
from frappe.utils import cint, flt, get_url_to_form, validate_email_address

from hrms.hr.doctype.interview.interview import get_interviewers

# Bitsol: Slack endpoints used for the recruitment hand-offs. The URLs can be overridden from
# System Settings (fields added by the Bitsol frappe fork); the token is always read from there.
SLACK_LOOKUP_BY_EMAIL_URL = "https://slack.com/api/users.lookupByEmail"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_TIMEOUT = 15


class DuplicationError(frappe.ValidationError):
	pass


class JobApplicant(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hrms.hr.doctype.cv_reviewer.cv_reviewer import CVReviewer
		from hrms.hr.doctype.scoring.scoring import Scoring

		applicant_name: DF.Data | None
		applicant_rating: DF.Rating
		applicant_status: DF.Literal[
			"Applied",
			"HR Screening",
			"Lead Screening",
			"Telephonic Screening",
			"CV Accepted",
			"CV Rejected",
			"Schedule Test",
			"Test Scheduled",
			"Schedule First Interview",
			"First Interview Scheduled",
			"Schedule Second Interview",
			"Second Interview Scheduled",
			"Assignment Submission",
			"Schedule Final Interview",
			"Final Interview Scheduled",
			"Offer Decision & Preparation",
			"Offer Accepted",
			"Background Check",
			"Joined",
			"Rejected",
		]
		country: DF.Link | None
		cover_letter: DF.Text | None
		currency: DF.Link | None
		cv_reviews: DF.Literal["", "Approved", "Rejected"]
		designation: DF.Link | None
		email_id: DF.Data | None
		employee_referral: DF.Link | None
		final_result: DF.Literal["", "Approved", "Rejected"]
		job_title: DF.Link | None
		lower_range: DF.Currency
		notes: DF.Text | None
		phone_number: DF.Data | None
		resume_attachment: DF.Attach | None
		resume_link: DF.Data | None
		scoring: DF.Table[Scoring]
		screening_from: DF.Datetime | None
		screening_to: DF.Datetime | None
		source: DF.Link | None
		source_name: DF.Link | None
		status: DF.Literal[
			"", "Open", "Replied", "Shortlisted", "Rejected", "Hold", "Accepted", "Active", "Probation", "Intern"
		]
		telephonic_interviewers: DF.Table[CVReviewer]
		telephonic_reviews: DF.Literal["", "Approved", "Rejected"]
		test_hardcopy: DF.Attach | None
		title: DF.Data | None
		upper_range: DF.Currency
	# end: auto-generated types

	def __init__(self, *args, **kwargs):
		self.previous_status = None
		super().__init__(*args, **kwargs)

	def onload(self):
		job_offer = frappe.get_all("Job Offer", filters={"job_applicant": self.name, "docstatus": ["!=", 2]})
		if job_offer:
			self.set_onload("job_offer", job_offer[0].name)

		employee = frappe.db.get_value("Employee", {"job_applicant": self.name}, "name") or ""
		self.set_onload("employee", employee)

	def autoname(self):
		self.name = self.email_id

		# Applicant can apply more than once for different job titles or reapply
		if frappe.db.exists("Job Applicant", self.name):
			self.name = append_number_if_name_exists("Job Applicant", self.name)

	def before_save(self):
		"""Bitsol hiring pipeline hand-offs.

		- CV Review Approved / Rejected moves the applicant to CV Accepted / CV Rejected.
		- Lead Screening: Slack DM to the Job Opening's CV Reviewers.
		- Telephonic Screening: e-mail + Slack DM to the telephonic interviewers
		  (interviewers added later are notified individually).
		- Joined: e-mail to HR. Rejected: e-mail to the candidate.
		"""
		old_doc = self.get_doc_before_save()
		if not old_doc:
			return

		previous_status = old_doc.applicant_status
		self.previous_status = previous_status

		# If CV Reviews is updated, update the applicant status
		if self.cv_reviews and self.cv_reviews != old_doc.cv_reviews:
			if self.cv_reviews == "Rejected":
				self.applicant_status = "CV Rejected"
			elif self.cv_reviews == "Approved":
				self.applicant_status = "CV Accepted"

		status_changed = previous_status != self.applicant_status
		previous_telephonic_reviewers = {row.email for row in old_doc.telephonic_interviewers if row.email}
		telephonic_reviewers = [row.email for row in self.telephonic_interviewers if row.email]
		new_telephonic_reviewers = [
			email for email in telephonic_reviewers if email not in previous_telephonic_reviewers
		]

		# applicants created without a Job Opening must still be saveable
		job_opening_title = None
		cv_reviewers = []
		if self.job_title and frappe.db.exists("Job Opening", self.job_title):
			job_opening_doc = frappe.get_doc("Job Opening", self.job_title)
			job_opening_title = job_opening_doc.get("job_title")
			cv_reviewers = [row.email for row in job_opening_doc.get("cv_reviewers", []) if row.email]

		if status_changed and self.applicant_status == "Lead Screening":
			send_slack_message(cv_reviewers, self.applicant_name, self.title, self.name, "Lead Screening")

		if self.applicant_status == "Telephonic Screening":
			# on entering the stage notify everyone, afterwards only the newly added interviewers
			recipients = telephonic_reviewers if status_changed else new_telephonic_reviewers
			if recipients:
				self.notify_telephonic_interviewers(recipients, job_opening_title)

		if status_changed and self.applicant_status == "Joined":
			self.notify_hr_about_joining()

		if status_changed and self.applicant_status == "Rejected":
			self.send_rejection_email(job_opening_title)

	def notify_telephonic_interviewers(self, recipients: list[str], job_opening_title: str | None):
		frappe.sendmail(
			recipients=recipients,
			create_notification_log=True,
			from_users=["Administrator"],
			for_users=["Administrator"],
			args={
				"name": self.applicant_name,
				"document_link": get_url_to_form("Job Applicant", self.name),
				"title": job_opening_title,
				"from": self.screening_from,
				"to": self.screening_to,
			},
			email_template_name="Telephonic Screening Email",
		)
		send_slack_message(
			recipients,
			self.applicant_name,
			self.title,
			self.name,
			"Telephonic Screening",
			self.screening_from,
			self.screening_to,
		)

	def notify_hr_about_joining(self):
		try:
			# users holding the "HR Manager" role profile: legacy single field (hidden in v16)
			# or the `role_profiles` multi-select table
			users = set(frappe.get_all("User", filters={"role_profile_name": "HR Manager"}, pluck="name"))
			users.update(
				frappe.get_all(
					"User Role Profile",
					filters={"role_profile": "HR Manager", "parenttype": "User"},
					pluck="parent",
				)
			)
			hr_manager_emails = []
			if users:
				hr_manager_emails = frappe.get_all(
					"User", filters={"enabled": 1, "name": ["in", list(users)]}, pluck="email"
				)
			hr_manager_emails = [email for email in hr_manager_emails if email]
			if not hr_manager_emails:
				return

			frappe.sendmail(
				recipients=hr_manager_emails,
				email_template_name="Applicant Joining",
				args={
					"name": self.name,
					"applicant_name": self.applicant_name,
					"title": self.title,
					"url": get_url_to_form("Job Applicant", self.name),
					"email": self.email_id,
				},
			)
		except Exception:
			frappe.log_error(title="Applicant Joined Notification Error")

	def send_rejection_email(self, job_opening_title: str | None):
		if not self.email_id:
			return

		# The Notification Log goes to Administrator (for_users): the candidate is not a system user,
		# so a log addressed to the candidate's e-mail address can never be delivered.
		try:
			frappe.sendmail(
				recipients=[self.email_id],
				create_notification_log=True,
				from_users=["Administrator"],
				for_users=["Administrator"],
				args={
					"name": self.applicant_name,
					"title": job_opening_title,
				},
				email_template_name="Rejection Email",
			)
		except Exception:
			frappe.log_error(title="Applicant Rejection Email Error")

	def validate(self):
		if self.email_id:
			validate_email_address(self.email_id, True)

		if self.employee_referral:
			self.set_status_for_employee_referral()

		if not self.applicant_name and self.email_id:
			guess = self.email_id.split("@")[0]
			self.applicant_name = " ".join([p.capitalize() for p in guess.split(".")])

	def before_insert(self):
		if self.job_title:
			job_opening = frappe.db.get_value(
				"Job Opening", self.job_title, ["status", "prevent_duplicate_applicant"], as_dict=True
			)
			if not job_opening:
				return

			if job_opening.status == "Closed":
				frappe.throw(
					_("Cannot create a Job Applicant against a closed Job Opening"), title=_("Not Allowed")
				)
			if job_opening.prevent_duplicate_applicant and self.email_id:
				if frappe.db.exists(
					"Job Applicant", {"email_id": self.email_id, "job_title": self.job_title}
				):
					frappe.throw(
						_("You have already applied for this position."),
						exc=DuplicationError,
						title=_("Duplicate Application"),
					)

		if frappe.flags.in_web_form and not self.source:
			self.source = "Website Listing"

	def set_status_for_employee_referral(self):
		emp_ref = frappe.get_doc("Employee Referral", self.employee_referral)
		if self.status in ["Open", "Replied", "Hold"]:
			emp_ref.db_set("status", "In Process")
		elif self.status in ["Accepted", "Rejected"]:
			emp_ref.db_set("status", self.status)


# Bitsol: the "Hiring Pipeline" board is driven by `applicant_status` (the Bitsol pipeline),
# not by upstream's `status`. One column per pipeline step, coloured like the list view.
KANBAN_FIELD = "applicant_status"
KANBAN_GREEN_STATUSES = (
	"CV Accepted",
	"Offer Decision & Preparation",
	"Offer Accepted",
	"Background Check",
	"Onboarding",
	"Joined",
)
KANBAN_RED_STATUSES = ("CV Rejected", "Rejected")


def get_kanban_columns() -> list[dict]:
	options = frappe.get_meta("Job Applicant").get_options(KANBAN_FIELD) or ""
	columns = []
	for status in options.split("\n"):
		if not status:
			continue
		if status in KANBAN_GREEN_STATUSES:
			indicator = "Green"
		elif status in KANBAN_RED_STATUSES:
			indicator = "Red"
		else:
			indicator = "Orange"
		columns.append({"column_name": status, "indicator": indicator})
	return columns


@frappe.whitelist()
def make_employee(source_name: str, target_doc: str | Document | None = None) -> Document:
	def set_missing_values(source, target):
		target.employee_name = source.applicant_name

		if not source.job_title:
			return

		job_opening = frappe.db.get_value(
			"Job Opening",
			source.job_title,
			["company", "department", "employment_type"],
			as_dict=True,
		)
		if not job_opening:
			return

		if job_opening.company:
			target.company = job_opening.company
		if job_opening.department:
			target.department = job_opening.department
		if job_opening.employment_type:
			target.employment_type = job_opening.employment_type

	return get_mapped_doc(
		"Job Applicant",
		source_name,
		{
			"Job Applicant": {
				"doctype": "Employee",
				"field_map": {
					"applicant_name": "first_name",
					"email_id": "personal_email",
					"phone_number": "cell_number",
					"currency": "salary_currency",
				},
				"field_no_map": ["status"],
			}
		},
		target_doc,
		set_missing_values,
	)


@frappe.whitelist(methods=["POST"])
def create_kanban_board(board_name: str) -> dict:
	frappe.has_permission("Job Applicant", throw=True)

	if frappe.db.exists("Kanban Board", board_name):
		return frappe.get_doc("Kanban Board", board_name).as_dict()

	board = frappe.new_doc("Kanban Board")
	board.kanban_board_name = board_name
	board.reference_doctype = "Job Applicant"
	board.field_name = KANBAN_FIELD
	board.private = 0
	board.fields = json.dumps(["title", "designation", "applicant_rating"])
	board.show_labels = 1

	for col in get_kanban_columns():
		board.append(
			"columns",
			{"column_name": col["column_name"], "indicator": col.get("indicator", ""), "status": "Active"},
		)

	board.insert(ignore_permissions=True)
	return board.as_dict()


@frappe.whitelist(methods=["POST"])
def create_interview(job_applicant: str, interview_type: str) -> Document:
	frappe.has_permission("Interview", ptype="create", throw=True)
	frappe.has_permission("Job Applicant", ptype="read", doc=job_applicant, throw=True)

	doc = frappe.get_doc("Job Applicant", job_applicant)

	round_designation = frappe.db.get_value("Interview Type", interview_type, "designation")

	if round_designation and doc.designation and round_designation != doc.designation:
		frappe.throw(
			_("Interview Type {0} is only applicable for the Designation {1}").format(
				interview_type, round_designation
			)
		)

	interview = frappe.new_doc("Interview")
	interview.interview_type = interview_type
	interview.job_applicant = doc.name
	interview.designation = doc.designation
	interview.resume_link = doc.resume_link
	interview.job_opening = doc.job_title
	set_bitsol_interview_details(interview, doc)

	interviewers = get_interviewers(interview_type)
	for d in interviewers:
		interview.append("interview_details", {"interviewer": d.interviewer})

	return interview


@frappe.whitelist(methods=["POST"])
def schedule_interview(
	job_applicant: str,
	interview_type: str,
	scheduled_on: str,
	from_time: str | None = None,
	to_time: str | None = None,
	interviewers: str | list | None = None,
	location: str | None = None,
) -> str:
	frappe.has_permission("Interview", ptype="create", throw=True)
	frappe.has_permission("Job Applicant", ptype="read", doc=job_applicant, throw=True)

	applicant = frappe.get_doc("Job Applicant", job_applicant)

	round_designation = frappe.db.get_value("Interview Type", interview_type, "designation")
	if round_designation and applicant.designation and round_designation != applicant.designation:
		frappe.throw(
			_("Interview Type {0} is only applicable for Designation {1}").format(
				frappe.bold(interview_type), frappe.bold(round_designation)
			)
		)

	interview = frappe.new_doc("Interview")
	interview.interview_type = interview_type
	interview.job_applicant = applicant.name
	interview.designation = applicant.designation
	interview.resume_link = applicant.resume_link
	interview.job_opening = applicant.job_title
	interview.scheduled_on = scheduled_on
	interview.from_time = from_time
	interview.to_time = to_time
	set_bitsol_interview_details(interview, applicant, location)

	if isinstance(interviewers, str):
		interviewers = json.loads(interviewers)

	for entry in interviewers or []:
		if entry.get("interviewer"):
			interview.append("interview_details", {"interviewer": entry["interviewer"]})

	interview.insert(ignore_permissions=True)

	return interview.name


def set_bitsol_interview_details(interview: Document, applicant: Document, location: str | None = None):
	"""Bitsol: copy the applicant details shown on the Interview (fields added by the Bitsol
	interview customisation; skipped when a field is not present on the Interview doctype)."""
	values = {
		"applicant_name": applicant.applicant_name,
		"job_title": applicant.title,
		"applicant_email": applicant.email_id,
		"resume_attachment": applicant.resume_attachment,
		"location": location,
	}
	for fieldname, value in values.items():
		if value and interview.meta.has_field(fieldname):
			interview.set(fieldname, value)


@frappe.whitelist()
def get_interview_details(job_applicant: str) -> dict:
	frappe.has_permission("Job Applicant", "read", job_applicant, throw=True)
	interview_details = frappe.db.get_all(
		"Interview",
		filters={"job_applicant": job_applicant, "docstatus": ["!=", 2]},
		fields=["name", "interview_type", "scheduled_on", "average_rating", "status"],
	)
	if not interview_details:
		return None

	interview_detail_map = {}
	# Bitsol stores Interview.average_rating as a 0-5 Float; upstream uses a Rating (0-1 fraction).
	# Scale only in the Rating case so the dashboard always receives a value out of `stars`.
	rating_field = frappe.get_meta("Interview").get_field("average_rating")
	is_rating_field = bool(rating_field and rating_field.fieldtype == "Rating")
	number_of_stars = (cint(rating_field.options) or 5) if is_rating_field else 5

	for detail in interview_details:
		if is_rating_field:
			detail.average_rating = detail.average_rating * number_of_stars if detail.average_rating else 0
		else:
			detail.average_rating = flt(detail.average_rating)

		interview_detail_map[detail.name] = detail

	return {"interviews": interview_detail_map, "stars": number_of_stars}


@frappe.whitelist()
def get_applicant_to_hire_percentage() -> dict:
	frappe.has_permission("Job Applicant", throw=True)

	total_applicants = frappe.db.count("Job Applicant")
	total_hired = frappe.db.count("Job Applicant", filters={"status": "Accepted"})

	return {
		"value": flt(total_hired) / flt(total_applicants) * 100 if total_applicants else 0,
		"fieldtype": "Percent",
	}


@frappe.whitelist()
def get_slack_user_id(email: str) -> str | None:
	"""Fetch the Slack user id for an e-mail address (Slack `users.lookupByEmail`)."""
	frappe.has_permission("Job Applicant", throw=True)
	return _get_slack_user_id(email)


def _get_slack_user_id(email: str | None) -> str | None:
	if not email:
		return None

	system_settings = frappe.get_cached_doc("System Settings")
	slack_token = system_settings.get("slack_token")
	if not slack_token:
		return None

	try:
		response = requests.get(
			SLACK_LOOKUP_BY_EMAIL_URL,
			headers={"Authorization": f"Bearer {slack_token}", "Content-Type": "application/json"},
			params={"email": email},
			timeout=SLACK_TIMEOUT,
		)
		data = response.json()
	except Exception:
		frappe.log_error(title="Slack user lookup failed")
		return None

	if data.get("ok"):
		return data["user"]["id"]

	frappe.logger("slack").warning(f"Slack user lookup failed for {email}: {data.get('error')}")
	return None


def send_slack_message(
	emails: list[str],
	applicant_name: str | None,
	job_title: str | None,
	docname: str,
	status: str,
	screening_from=None,
	screening_to=None,
):
	"""Send an individual Slack DM to each e-mail address for the given pipeline step.

	Never raises: a Slack outage must not block saving the Job Applicant.
	"""
	if not emails:
		return

	system_settings = frappe.get_cached_doc("System Settings")
	slack_token = system_settings.get("slack_token")
	if not slack_token:
		return

	post_message_url = system_settings.get("slack_post_message_url") or SLACK_POST_MESSAGE_URL
	document_link = get_url_to_form("Job Applicant", docname)
	headers = {"Authorization": f"Bearer {slack_token}", "Content-Type": "application/json"}

	for email in emails:
		user_id = _get_slack_user_id(email)
		if not user_id:
			continue

		if status == "Lead Screening":
			message = (
				f"Hello <@{user_id}>, please review the CV of {applicant_name} for the position {job_title}."
				f"\nCandidate details can be accessed via\n {document_link}."
			)
		elif status == "Telephonic Screening":
			message = (
				f"Hello <@{user_id}>, please review the profile of {applicant_name} and conduct a telephonic"
				f" screening interview for the position {job_title}."
				f"\nCandidate details can be accessed via\n {document_link}. "
				f"\nPlease review within the timeframe of {screening_from} to {screening_to}."
			)
		else:
			continue

		try:
			response = requests.post(
				post_message_url,
				headers=headers,
				data=json.dumps({"channel": user_id, "text": message}),
				timeout=SLACK_TIMEOUT,
			)
			result = response.json()
			if not result.get("ok"):
				frappe.logger("slack").warning(f"Slack message to {email} failed: {result.get('error')}")
		except Exception:
			frappe.log_error(title="Slack message failed")


@frappe.whitelist()
def fetch_and_save_all_slack_ids() -> int:
	"""Back-fill Employee.custom_slack_user_id (site-level custom field) from Slack."""
	frappe.only_for(("System Manager", "HR Manager"))

	if not frappe.get_meta("Employee").has_field("custom_slack_user_id"):
		frappe.throw(_("Custom field {0} does not exist on Employee").format("custom_slack_user_id"))

	employees = frappe.get_all(
		"Employee",
		filters={"custom_slack_user_id": ["is", "not set"], "user_id": ["is", "set"]},
		fields=["name", "user_id"],
	)
	updated = 0
	for emp in employees:
		email = frappe.db.get_value("User", emp.user_id, "email")
		if not email:
			continue
		slack_user_id = _get_slack_user_id(email)
		if slack_user_id:
			frappe.db.set_value("Employee", emp.name, "custom_slack_user_id", slack_user_id)
			updated += 1

	return updated


@frappe.whitelist()
def get_feedback_by_round(applicant: str) -> list[dict]:
	"""Submitted Interview Feedback of an applicant, grouped by interview (type)."""
	frappe.has_permission("Job Applicant", "read", applicant, throw=True)

	interviews = frappe.get_all(
		"Interview",
		filters={"job_applicant": applicant, "docstatus": ["!=", 2]},
		fields=["name", "interview_type"],
		order_by="creation asc",
	)

	# Bitsol stores Interview Feedback.average_rating as a 0-5 Float; upstream uses a Rating (0-1).
	rating_field = frappe.get_meta("Interview Feedback").get_field("average_rating")
	multiplier = 5 if (rating_field and rating_field.fieldtype == "Rating") else 1

	interview_feedback = frappe.qb.DocType("Interview Feedback")
	employee = frappe.qb.DocType("Employee")
	feedback_by_round = []

	for interview in interviews:
		feedback_data = (
			frappe.qb.from_(interview_feedback)
			.select(
				interview_feedback.name,
				interview_feedback.modified.as_("added_on"),
				interview_feedback.interviewer.as_("user"),
				interview_feedback.feedback,
				interview_feedback.result,
				interview_feedback.average_rating.as_("total_score"),
				employee.employee_name.as_("reviewer_name"),
				employee.designation.as_("reviewer_designation"),
			)
			.left_join(employee)
			.on(interview_feedback.interviewer == employee.user_id)
			.where((interview_feedback.interview == interview.name) & (interview_feedback.docstatus == 1))
			.orderby(interview_feedback.modified)
		).run(as_dict=True)

		if not feedback_data:
			continue

		for row in feedback_data:
			row.total_score = flt(row.total_score) * multiplier

		feedback_by_round.append(
			{
				# `interview_round` is the key the grouped_feedback_history template reads
				# (v15 name); in v16 the Interview Round doctype is the Interview Type.
				"interview_round": interview.interview_type,
				"interview_type": interview.interview_type,
				"feedback": feedback_data,
			}
		)

	return feedback_by_round
