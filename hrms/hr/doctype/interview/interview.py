# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import datetime
import json
import uuid

import requests

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Avg
from frappe.utils import cint, cstr, get_datetime, get_link_to_form, get_time, getdate, nowtime

# Bitsol: Google Meet space creation (service account with domain-wide delegation)
SCOPES = [
	"https://www.googleapis.com/auth/meetings.space.created",
	"https://www.googleapis.com/auth/meetings.space.readonly",
]
MEET_IMPERSONATED_USER = "hr@bitsol.tech"
CALENDAR_ORGANIZER_EMAIL = "no-reply@bitsol.tech"
CALENDAR_TIMEZONE = "Asia/Karachi"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class DuplicateInterviewRoundError(frappe.ValidationError):
	pass


class Interview(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hrms.hr.doctype.interview_detail.interview_detail import InterviewDetail

		amended_from: DF.Link | None
		applicant_email: DF.Data | None
		applicant_name: DF.Data | None
		average_rating: DF.Float
		calendar_event_uid: DF.Data | None
		designation: DF.Link | None
		expected_average_rating: DF.Float
		from_time: DF.Time
		interview_details: DF.Table[InterviewDetail]
		interview_summary: DF.Text | None
		interview_type: DF.Link
		job_applicant: DF.Link
		job_opening: DF.Link | None
		job_title: DF.Data | None
		location: DF.Literal["Onsite", "Remote"]
		meeting_link: DF.Data | None
		reminded: DF.Check
		resume_attachment: DF.Attach | None
		resume_link: DF.Data | None
		scheduled_on: DF.Date
		sequence_number: DF.Int
		status: DF.Literal["Pending", "Under Review", "Cleared", "Rejected", "Cancelled"]
		to_time: DF.Time
	# end: auto-generated types

	def validate(self):
		self.validate_duplicate_interview()
		self.validate_designation()

	def on_submit(self):
		if self.status not in ["Cleared", "Rejected"]:
			frappe.throw(
				_("Only Interviews with Cleared or Rejected status can be submitted."),
				title=_("Not Allowed"),
			)
		# Bitsol: the "update Job Applicant" dialog is not shown on submit

	def after_insert(self):
		"""Bitsol: create the Google Meet link and send the calendar invite to interviewers,
		recruiters and the candidate."""
		meeting_link = get_meeting_link()
		self.db_set("meeting_link", meeting_link)

		# Generate and store the calendar event UID once; reschedules reuse it
		if not self.calendar_event_uid:
			self.db_set({"calendar_event_uid": str(uuid.uuid4()), "sequence_number": 0})

		recipients = list(set(get_recipients(self.name) + get_recruiter_emails()))

		try:
			ics_file = self.create_ics_file(recipients, meeting_link)
			attachment = {"fname": "event.ics", "fcontent": ics_file}

			email_template_name = self.get_scheduling_email_template()
			email_args = self.get_scheduling_email_args("scheduled")

			# Rendered here (instead of the frappe fork's `sendmail(email_template_name=...)`)
			# so that the same content goes to the email, Notification Log and Communication.
			email_template = frappe.get_doc("Email Template", email_template_name)
			subject = email_template.get_formatted_subject(email_args)
			message = email_template.get_formatted_response(email_args)
			email_content = frappe.render_template(email_template.response or "", email_args)

			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				header=subject,
				message=message,
				attachments=[attachment],
			)

			log_subject = f"Interview Scheduled - {email_args['interview_type']}"
			self.create_notification_logs(recipients, log_subject, email_content)
			log_email_in_comments(
				doc=self,
				subject=f"Interview Scheduled – {email_args['interview_type']}",
				html_content=email_content,
				recipients=recipients,
			)
		except Exception as e:
			frappe.log_error(message=f"Error sending email: {e}", title="Interview Email Send Error")

	def get_scheduling_email_template(self) -> str:
		return "Interview Scheduling Template" if self.location == "Remote" else "Interview on site"

	def get_scheduling_email_args(self, schedule_status: str) -> dict:
		return {
			"name": self.applicant_name,
			"title": self.job_title,
			"location": self.location,
			"date": self.scheduled_on,
			# `interview_round` is kept for the existing Email Templates; Interview Round was
			# merged into Interview Type in v16
			"interview_round": self.interview_type,
			"time": self.parse_time(self.from_time).strftime("%I:%M %p"),
			"meeting_link": self.meeting_link,
			"interview_type": "Remote" if self.location == "Remote" else "On-Site",
			"schedule_status": schedule_status,
		}

	def create_notification_logs(self, recipients: list[str], subject: str, email_content: str) -> None:
		"""In-app notification for every recipient that is a system user (not the candidate)."""
		for user in set(recipients):
			if not user or user == self.applicant_email or not frappe.db.exists("User", user):
				continue
			try:
				frappe.get_doc(
					{
						"doctype": "Notification Log",
						"type": "Alert",
						"for_user": user,
						"subject": subject,
						"email_content": email_content,
						"document_type": "Interview",
						"document_name": self.name,
					}
				).insert(ignore_permissions=True)
			except Exception:
				frappe.log_error(title="Interview Notification Log Error")

	def validate_duplicate_interview(self):
		# Bitsol: drafts count as duplicates too, and the check is per job opening
		duplicate_interview = frappe.db.exists(
			"Interview",
			{
				"job_applicant": self.job_applicant,
				"interview_type": self.interview_type,
				"job_opening": self.job_opening or ["is", "not set"],
				"docstatus": ["in", [0, 1]],
				"name": ["!=", self.name],
			},
		)

		if duplicate_interview:
			frappe.throw(
				_(
					"Job Applicants are not allowed to appear twice for the same Interview Type. Interview {0} already scheduled for Job Applicant {1} for Job Opening {2} against ID {3}"
				).format(
					frappe.bold(get_link_to_form("Interview", duplicate_interview)),
					frappe.bold(self.job_applicant),
					frappe.bold(self.job_opening),
					frappe.bold(self.name),
				)
			)

	def validate_designation(self):
		applicant_designation = frappe.db.get_value("Job Applicant", self.job_applicant, "designation")
		if self.designation:
			if self.designation != applicant_designation:
				frappe.throw(
					_(
						"Interview Type {0} is only for Designation {1}. Job Applicant has applied for the role {2}"
					).format(self.interview_type, frappe.bold(self.designation), applicant_designation),
					exc=DuplicateInterviewRoundError,
				)
		else:
			self.designation = applicant_designation

	def show_job_applicant_update_dialog(self):
		job_applicant_status = self.get_job_applicant_status()
		if not job_applicant_status:
			return

		job_application_name = frappe.db.get_value("Job Applicant", self.job_applicant, "applicant_name")

		frappe.msgprint(
			_("Do you want to update the Job Applicant {0} as {1} based on this interview result?").format(
				frappe.bold(job_application_name), frappe.bold(job_applicant_status)
			),
			title=_("Update Job Applicant"),
			primary_action={
				"label": _("Mark as {0}").format(job_applicant_status),
				"server_action": "hrms.hr.doctype.interview.interview.update_job_applicant_status",
				"args": {"job_applicant": self.job_applicant, "status": job_applicant_status},
			},
		)

	def get_job_applicant_status(self) -> str | None:
		status_map = {"Cleared": "Active", "Rejected": "Rejected"}
		return status_map.get(self.status, None)

	@frappe.whitelist(methods=["POST"])
	def reschedule_interview(
		self, scheduled_on: datetime.date, from_time: datetime.time, to_time: datetime.time
	) -> None:
		if (
			getdate(scheduled_on) == getdate(self.scheduled_on)
			and get_time(from_time) == get_time(self.from_time)
			and get_time(to_time) == get_time(self.to_time)
		):
			frappe.msgprint(
				_("No changes found in timings."), indicator="orange", title=_("Interview Not Rescheduled")
			)
			return

		# Update interview timings in DB
		self.db_set({"scheduled_on": scheduled_on, "from_time": from_time, "to_time": to_time})
		self.notify_update()

		# Bitsol: resend the invite (same calendar UID, higher sequence) using the scheduling templates
		self.reload()

		recipients = list(set(get_recipients(self.name) + get_recruiter_emails()))

		try:
			interview_template = frappe.get_doc("Email Template", self.get_scheduling_email_template())
			email_args = self.get_scheduling_email_args("rescheduled")
			message = frappe.render_template(interview_template.response or "", email_args)

			ics_file = self.create_ics_file(recipients, self.meeting_link)
			attachment = {"fname": "event.ics", "fcontent": ics_file}

			frappe.sendmail(
				recipients=recipients,
				sender=frappe.db.get_single_value("HR Settings", "sender_email") or "",
				subject=_("Rescheduled: {0} Interview for the position of {1} at {2}, at {3}").format(
					self.location, self.job_title, self.scheduled_on, email_args["time"]
				),
				message=message,
				reference_doctype=self.doctype,
				reference_name=self.name,
				attachments=[attachment],
			)
			log_email_in_comments(
				doc=self,
				subject=f"Interview Rescheduled – {email_args['interview_type']}",
				html_content=message,
				recipients=recipients,
			)
		except Exception:
			frappe.log_error(title="Interview Reschedule Email Error")
			frappe.msgprint(
				_(
					"Failed to send the Interview Reschedule notification. Please configure your email account."
				)
			)

		frappe.msgprint(_("Interview Rescheduled successfully"), indicator="green")

	def on_discard(self):
		self.db_set("status", "Cancelled")

	def parse_time(self, time_str) -> datetime.time:
		"""Accepts "HH:MM:SS[.ffffff]" strings, `datetime.time` (v16 typed endpoints) and the
		`datetime.timedelta` values returned when the document is reloaded from the database."""
		return get_time(time_str)

	def create_ics_file(self, recipients, meeting_link):
		event_date = getdate(self.scheduled_on)
		start_time = datetime.datetime.combine(event_date, self.parse_time(self.from_time))
		end_time = datetime.datetime.combine(event_date, self.parse_time(self.to_time))

		# Define event details
		event_name = f"{self.applicant_name}-({self.job_title})-{self.location} Interview"
		event_description = "Candidate Interview"
		timezone = CALENDAR_TIMEZONE

		# Use the existing UID if available, otherwise generate a new one
		calendar_uid = self.calendar_event_uid or frappe.db.get_value(
			"Interview", self.name, "calendar_event_uid"
		)
		if not calendar_uid:
			calendar_uid = str(uuid.uuid4())
			self.db_set({"calendar_event_uid": calendar_uid, "sequence_number": 0})
		else:
			# Ensure the object has the value from the database
			self.calendar_event_uid = calendar_uid

		# Increment the sequence number so calendar clients update the same event
		current_sequence = cint(frappe.db.get_value("Interview", self.name, "sequence_number"))
		new_sequence = current_sequence + 1
		self.db_set("sequence_number", new_sequence)

		now = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")

		# Create ICS content
		ics_content = f"""BEGIN:VCALENDAR
PRODID:-//Google Inc//Google Calendar 70.9054//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VTIMEZONE
TZID:{timezone}
X-LIC-LOCATION:{timezone}
BEGIN:STANDARD
TZOFFSETFROM:+0500
TZOFFSETTO:+0500
TZNAME:PKT
DTSTART:19700101T000000
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTART;TZID={timezone}:{start_time.strftime("%Y%m%dT%H%M%S")}
DTEND;TZID={timezone}:{end_time.strftime("%Y%m%dT%H%M%S")}
DTSTAMP:{now}
ORGANIZER;CN=Bitsol:mailto:{CALENDAR_ORGANIZER_EMAIL}
UID:{calendar_uid}
SEQUENCE:{new_sequence}
X-GOOGLE-CONFERENCE:{meeting_link or ""}
CREATED:{now}
DESCRIPTION:{event_description}
LAST-MODIFIED:{now}
STATUS:CONFIRMED
SUMMARY:{event_name}
TRANSP:OPAQUE
"""

		for attendee in recipients:
			ics_content += f"""ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE;CN={attendee};X-NUM-GUESTS=0:mailto:{attendee}\n"""

		ics_content += "END:VEVENT\nEND:VCALENDAR"
		return ics_content


@frappe.whitelist()
def get_interviewers(interview_type: str) -> list[dict]:
	frappe.has_permission("Interview Type", "read", interview_type, throw=True)
	return frappe.get_all("Interviewer", filters={"parent": interview_type}, fields=["user as interviewer"])


def log_email_in_comments(doc, subject, html_content, recipients):
	"""Bitsol: keep a private Communication on the document timeline for every invite sent."""
	frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": "Sent",
			"subject": subject,
			"content": html_content,  # full HTML body
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"recipients": ", ".join(recipients),
			"sender": frappe.session.user,
			"private": 1,
		}
	).insert(ignore_permissions=True)


def get_recipients(name, for_feedback=0):
	interview = frappe.get_doc("Interview", name)
	# Interviewers from the Interview Details table (explicit entries)
	details = [d.interviewer for d in interview.interview_details if d.interviewer]

	# Bitsol: interviewers configured on the Interview Type (v15: Interview Round), even if
	# they were not explicitly added to the interview
	type_interviewers = frappe.get_all(
		"Interviewer",
		filters={"parent": interview.interview_type, "parenttype": "Interview Type"},
		pluck="user",
	)

	# Combine both lists and remove duplicates
	recipients = list({d for d in details + type_interviewers if d})

	if for_feedback:
		feedback_given_interviewers = frappe.get_all(
			"Interview Feedback", filters={"interview": name, "docstatus": 1}, pluck="interviewer"
		)
		recipients = [d for d in recipients if d not in feedback_given_interviewers]
	else:
		applicant_email = frappe.db.get_value("Job Applicant", interview.job_applicant, "email_id")
		if applicant_email and applicant_email not in recipients:
			recipients.append(applicant_email)

	return recipients


@frappe.whitelist()
def get_feedback(interview: str) -> list[dict]:
	frappe.has_permission("Interview", "read", interview, throw=True)
	frappe.has_permission("Interview Feedback", "read", throw=True)

	interview_feedback = frappe.qb.DocType("Interview Feedback")
	employee = frappe.qb.DocType("Employee")

	return (
		frappe.qb.from_(interview_feedback)
		.select(
			interview_feedback.name,
			interview_feedback.modified.as_("added_on"),
			interview_feedback.interviewer.as_("user"),
			interview_feedback.feedback,
			interview_feedback.average_rating.as_("total_score"),
			employee.employee_name.as_("reviewer_name"),
			employee.designation.as_("reviewer_designation"),
		)
		.left_join(employee)
		.on(interview_feedback.interviewer == employee.user_id)
		.where((interview_feedback.interview == interview) & (interview_feedback.docstatus == 1))
		.orderby(interview_feedback.creation)
	).run(as_dict=True)


@frappe.whitelist()
def get_skill_wise_average_rating(interview: str) -> list[dict]:
	frappe.has_permission("Interview", "read", interview, throw=True)
	skill_assessment = frappe.qb.DocType("Skill Assessment")
	interview_feedback = frappe.qb.DocType("Interview Feedback")
	return (
		frappe.qb.select(
			skill_assessment.skill,
			Avg(skill_assessment.rating).as_("rating"),
		)
		.from_(skill_assessment)
		.join(interview_feedback)
		.on(skill_assessment.parent == interview_feedback.name)
		.where((interview_feedback.interview == interview) & (interview_feedback.docstatus == 1))
		.groupby(skill_assessment.skill)
		.orderby(skill_assessment.idx)
	).run(as_dict=True)


@frappe.whitelist(methods=["POST"])
def update_job_applicant_status(status: str, job_applicant: str):
	try:
		if not job_applicant:
			frappe.throw(_("Please specify the job applicant to be updated."))

		doc = frappe.get_doc("Job Applicant", job_applicant)
		doc.status = status
		doc.save()

		frappe.msgprint(
			_("Updated the Job Applicant status to {0}").format(doc.status),
			alert=True,
			indicator="green",
		)
	except Exception:
		frappe.log_error("Failed to update Job Applicant status")
		frappe.msgprint(
			_("Failed to update the Job Applicant status"),
			alert=True,
			indicator="red",
		)


def get_recruiter_emails():
	"""Bitsol: every enabled user with the Recruiter role is copied on interview mails."""
	user_ids = list(
		set(frappe.get_all("Has Role", filters={"role": "Recruiter", "parenttype": "User"}, pluck="parent"))
	)
	if not user_ids:
		return []

	users = frappe.get_all("User", filters={"name": ["in", user_ids], "enabled": 1}, fields=["email"])
	return [user.email for user in users if user.email]


def send_interview_reminder():
	reminder_settings = frappe.db.get_value(
		"HR Settings",
		"HR Settings",
		["send_interview_reminder", "interview_reminder_template", "hiring_sender_email"],
		as_dict=True,
	)

	if not cint(reminder_settings.send_interview_reminder):
		return

	remind_before = cstr(frappe.db.get_single_value("HR Settings", "remind_before")) or "01:00:00"
	remind_before = datetime.datetime.strptime(remind_before, "%H:%M:%S")
	reminder_date_time = datetime.datetime.now() + datetime.timedelta(
		hours=remind_before.hour, minutes=remind_before.minute, seconds=remind_before.second
	)

	interviews = frappe.get_all(
		"Interview",
		filters=[
			["scheduled_on", "between", [datetime.datetime.now(), reminder_date_time]],
			["status", "=", "Pending"],
			["reminded", "=", 0],
			["docstatus", "!=", 2],
		],
	)

	interview_template = frappe.get_doc("Email Template", reminder_settings.interview_reminder_template)

	for d in interviews:
		doc = frappe.get_doc("Interview", d.name)
		formatted_date = getdate(doc.scheduled_on).strftime("%B %d, %Y")  # Example: April 22, 2025
		formatted_time = get_time(doc.from_time).strftime("%I:%M %p")  # Example: 03:27 PM
		context = doc.as_dict()
		context["formatted_date"] = formatted_date
		context["formatted_time"] = formatted_time
		# kept for Email Templates written before Interview Round was merged into Interview Type
		context["interview_round"] = doc.interview_type
		message = frappe.render_template(interview_template.response, context)

		# Bitsol: interviewers who have not given feedback yet + recruiters; the candidate gets
		# a separate reminder
		recipients = list(set(get_recipients(doc.name, for_feedback=1) + get_recruiter_emails()))
		if recipients:
			frappe.sendmail(
				sender=reminder_settings.hiring_sender_email,
				recipients=recipients,
				subject=interview_template.subject,
				message=message,
				reference_doctype=doc.doctype,
				reference_name=doc.name,
			)
		send_slack_message(
			recipients, doc.location, doc.applicant_name, doc.job_title, formatted_date, formatted_time
		)
		send_candidate_reminders(doc, formatted_date, formatted_time)

		doc.db_set("reminded", 1)


def send_slack_message(emails, location, applicant_name, job_title, scheduled_on, from_time):
	"""Bitsol: Slack DM reminder. Needs `System Settings.slack_token` (Bitsol frappe fork) and the
	`Employee.custom_slack_user_id` custom field; silently skipped when either is missing."""
	slack_token = frappe.get_single("System Settings").get("slack_token")
	if not slack_token or not frappe.get_meta("Employee").has_field("custom_slack_user_id"):
		return

	headers = {"Authorization": f"Bearer {slack_token}", "Content-Type": "application/json"}

	for email in emails:
		slack_user_id = frappe.db.get_value("Employee", {"user_id": email}, "custom_slack_user_id")
		if not slack_user_id:
			# no Employee / Slack ID linked to this user (e.g. the candidate)
			continue

		message = (
			f"Hey <@{slack_user_id}>, Just a quick reminder — you have {location} Interview scheduled with"
			f" {applicant_name} for the position {job_title} on {scheduled_on} {from_time}.\n"
			"Let me know if you need anything before the call. Good luck!"
		)
		payload = {"channel": slack_user_id, "text": message}
		try:
			response = requests.post(
				SLACK_POST_MESSAGE_URL, headers=headers, data=json.dumps(payload), timeout=10
			)
			if not response.ok:
				frappe.log_error(
					title="Interview Slack Reminder Error",
					message=f"Slack API error for {email}: {response.text}",
				)
		except Exception:
			frappe.log_error(title="Interview Slack Reminder Error")


def send_candidate_reminders(interview, formatted_date, formatted_time):
	if not interview.applicant_email:
		return

	try:
		reminder_template = frappe.get_doc("Email Template", "Interview Reminder - Candidate")
		# Prepare context for the template
		args = {
			"job_title": interview.job_title,
			"applicant_name": interview.applicant_name,
			"location": interview.location,
			# `interview_round` is kept for the existing Email Template
			"interview_round": interview.interview_type,
			"interview_type": interview.interview_type,
			"scheduled_on": formatted_date,
			"from_time": formatted_time,
			"meeting_link": interview.meeting_link,
		}
		message = frappe.render_template(reminder_template.response, args)
		subject = frappe.render_template(reminder_template.subject, args)
		frappe.sendmail(
			recipients=[interview.applicant_email],
			message=message,
			subject=subject,
			reference_doctype=interview.doctype,
			reference_name=interview.name,
		)
	except Exception as e:
		frappe.log_error(
			title="Interview Candidate Reminder Error",
			message=f"Error sending interview reminder to {interview.applicant_email}: {e}",
		)


def send_daily_feedback_reminder():
	reminder_settings = frappe.db.get_value(
		"HR Settings",
		"HR Settings",
		[
			"send_interview_feedback_reminder",
			"feedback_reminder_notification_template",
			"hiring_sender_email",
		],
		as_dict=True,
	)

	if not cint(reminder_settings.send_interview_feedback_reminder):
		return

	interview_feedback_template = frappe.get_doc(
		"Email Template", reminder_settings.feedback_reminder_notification_template
	)

	interviews = frappe.get_all(
		"Interview",
		filters={
			"status": "Under Review",
			"docstatus": ["!=", 2],
			"scheduled_on": ["<=", getdate()],
			"to_time": ["<=", nowtime()],
		},
		pluck="name",
	)

	for interview in interviews:
		recipients = get_recipients(interview, for_feedback=1)

		doc = frappe.get_doc("Interview", interview)
		context = doc.as_dict()

		message = frappe.render_template(interview_feedback_template.response, context)

		if len(recipients):
			frappe.sendmail(
				sender=reminder_settings.hiring_sender_email,
				recipients=recipients,
				subject=interview_feedback_template.subject,
				message=message,
				reference_doctype="Interview",
				reference_name=interview,
			)


@frappe.whitelist()
def get_expected_skill_set(interview_type: str) -> list[dict]:
	frappe.has_permission("Interview Type", "read", interview_type, throw=True)
	return frappe.get_all(
		"Expected Skill Set", filters={"parent": interview_type}, fields=["skill"], order_by="idx"
	)


@frappe.whitelist(methods=["POST"])
def create_interview_feedback(data: str | dict, interview_name: str, interviewer: str, job_applicant: str):
	if isinstance(data, str):
		data = json.loads(data)
	data = frappe._dict(data)

	if frappe.session.user != interviewer:
		frappe.throw(_("Only Interviewer Are allowed to submit Interview Feedback"))

	interview_feedback = frappe.new_doc("Interview Feedback")
	interview_feedback.interview = interview_name
	interview_feedback.interviewer = interviewer
	interview_feedback.job_applicant = job_applicant

	# Bitsol: skills are optional in the feedback dialog
	for d in data.skill_set or []:
		d = frappe._dict(d)
		interview_feedback.append("skill_assessment", d)

	interview_feedback.feedback = data.feedback
	interview_feedback.result = data.result

	interview_feedback.save()
	interview_feedback.submit()

	frappe.msgprint(
		_("Interview Feedback {0} submitted successfully").format(
			get_link_to_form("Interview Feedback", interview_feedback.name)
		)
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_interviewer_list(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict
) -> list:
	filters = [
		["Has Role", "parent", "like", f"%{txt}%"],
		["Has Role", "role", "=", "interviewer"],
		["Has Role", "parenttype", "=", "User"],
	]

	if filters and isinstance(filters, list):
		filters.extend(filters)

	return frappe.get_all(
		"Has Role",
		limit_start=start,
		limit_page_length=page_len,
		filters=filters,
		fields=["parent"],
		as_list=1,
	)


@frappe.whitelist()
def get_events(start: str, end: str, filters: str | None = None):
	"""Returns events for Gantt / Calendar view rendering.

	:param start: Start date-time.
	:param end: End date-time.
	:param filters: Filters (JSON).
	"""
	from frappe.desk.calendar import get_event_conditions

	events = []

	event_color = {
		"Pending": "#fff4f0",
		"Under Review": "#d3e8fc",
		"Cleared": "#eaf5ed",
		"Rejected": "#fce7e7",
	}

	conditions = get_event_conditions("Interview", filters)

	# nosemgrep: frappe-semgrep-rules.rules.frappe-using-db-sql
	interviews = frappe.db.sql(
		f"""
			SELECT DISTINCT
				`tabInterview`.name, `tabInterview`.job_applicant, `tabInterview`.interview_type,
				`tabInterview`.scheduled_on, `tabInterview`.status, `tabInterview`.from_time as from_time,
				`tabInterview`.to_time as to_time
			from
				`tabInterview`
			where
				(`tabInterview`.scheduled_on between %(start)s and %(end)s)
				and docstatus != 2
				{conditions}
			""",
		{"start": start, "end": end},
		as_dict=True,
		update={"allDay": 0},
	)

	for d in interviews:
		subject_data = []
		for field in ["name", "job_applicant", "interview_type"]:
			if not d.get(field):
				continue
			subject_data.append(d.get(field))

		color = event_color.get(d.status)
		interview_data = {
			"from": get_datetime(
				"{scheduled_on} {from_time}".format(
					scheduled_on=d.scheduled_on, from_time=d.from_time or "00:00:00"
				)
			),
			"to": get_datetime(
				"{scheduled_on} {to_time}".format(
					scheduled_on=d.scheduled_on, to_time=d.to_time or "00:00:00"
				)
			),
			"name": d.name,
			"subject": "\n".join(subject_data),
			"color": color if color else "#89bcde",
		}

		events.append(interview_data)

	return events


def get_meeting_link():
	"""Bitsol: create a Google Meet space with the service account stored in
	`FCM Notification Settings.google_service_account` (fcm_notification app), impersonating
	the HR mailbox. Returns None (and tells the user) when it cannot be created."""
	try:
		# imported lazily so that hrms loads without the google packages installed
		from google.apps import meet_v2
		from google.oauth2.service_account import Credentials

		server_key = frappe.db.get_single_value("FCM Notification Settings", "google_service_account")
		if isinstance(server_key, str):
			server_key = json.loads(server_key)
		creds = Credentials.from_service_account_info(server_key, scopes=SCOPES)
		impersonated_creds = creds.with_subject(MEET_IMPERSONATED_USER)
	except Exception as e:
		frappe.msgprint(f"Error during authorization: {e}")
		return None

	try:
		client = meet_v2.SpacesServiceClient(credentials=impersonated_creds)
		request = meet_v2.CreateSpaceRequest()
		response = client.create_space(request=request)
		return response.meeting_uri
	except Exception as e:
		frappe.msgprint(f"Error creating space: {e}")
		return None
