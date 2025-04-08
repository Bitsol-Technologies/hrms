# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import append_number_if_name_exists
from frappe.utils import flt, validate_email_address
import requests 

from hrms.hr.doctype.interview.interview import get_interviewers


class DuplicationError(frappe.ValidationError):
	pass


class JobApplicant(Document):
	def __init__(self, *args, **kwargs):
		self.previous_status = None
		super().__init__(*args, **kwargs)

	def onload(self):
		job_offer = frappe.get_all("Job Offer", filters={"job_applicant": self.name})
		if job_offer:
			self.get("__onload").job_offer = job_offer[0].name

	def autoname(self):
		self.name = self.email_id

		# Applicant can apply more than once for different job titles or reapply
		if frappe.db.exists("Job Applicant", self.name):
			self.name = append_number_if_name_exists("Job Applicant", self.name)

	def before_save(self):
		"""
		Send Slack message to CV Reviewers and Telephonic Interviewers
		when the applicant status changes to Lead Screening or Telephonic Screening
		"""
		old_doc = self.get_doc_before_save()
		if old_doc:
			previous_cv_reviews = frappe.db.get_value(self.doctype, self.name, 'cv_reviews', as_dict=True) or {}
			previous_status = old_doc.applicant_status
			previous_telephonic_reviewers = {lead.email for lead in old_doc.telephonic_interviewers} if old_doc.telephonic_interviewers else set()

			# If CV Reviews is updated, update the applicant status
			if self.cv_reviews and self.cv_reviews != previous_cv_reviews.get("cv_reviews"):
				if self.cv_reviews == "Rejected":
					self.applicant_status = "CV Rejected"
				elif self.cv_reviews == "Approved":
					self.applicant_status = "CV Accepted"

			job_opening_doc = frappe.get_doc("Job Opening", self.job_title)
			cv_reviewers = [lead.email for lead in job_opening_doc.cv_reviewers]
			telephonic_reviewers = [lead.email for lead in self.telephonic_interviewers]

			# Convert both lists to sets before performing set difference
			previous_telephonic_reviewers_set = set(previous_telephonic_reviewers) if previous_telephonic_reviewers else set()
			current_telephonic_reviewers_set = set(telephonic_reviewers) if telephonic_reviewers else set()

			# Identify new interviewers who were not in the previous list
			new_telephonic_reviewers = list(current_telephonic_reviewers_set - previous_telephonic_reviewers_set)

			if previous_status != self.applicant_status and self.applicant_status == "Lead Screening":
				send_slack_message(cv_reviewers, self.applicant_name, self.title, self.name, "Lead Screening")

			if previous_status != self.applicant_status and self.applicant_status == "Telephonic Screening":
				frappe.sendmail(
					recipients=telephonic_reviewers,
					create_notification_log=True,
					from_users=["Administrator"],
					for_users=["Administrator"],
					args={
						"name": self.applicant_name,
						"document_link": frappe.utils.get_url_to_form("Job Applicant", self.name),
						"title": job_opening_doc.get("job_title"),
						"from": self.screening_from,
						"to": self.screening_to,
					},
					email_template_name="Telephonic Screening Email",
				)
				send_slack_message(telephonic_reviewers, self.applicant_name, self.title, self.name, "Telephonic Screening",self.screening_from, self.screening_to)

			# Notify only newly added Telephonic Interviewers
			if new_telephonic_reviewers and self.applicant_status == "Telephonic Screening":
				frappe.sendmail(
					recipients=new_telephonic_reviewers,
					create_notification_log=True,
					from_users=["Administrator"],
					for_users=["Administrator"],
					args={
						"name": self.applicant_name,
						"document_link": frappe.utils.get_url_to_form("Job Applicant", self.name),
						"title": job_opening_doc.get("job_title"),
						"from": self.screening_from,
						"to": self.screening_to,
					},
					email_template_name="Telephonic Screening Email",
					)
				send_slack_message(new_telephonic_reviewers, self.applicant_name, self.title, self.name, "Telephonic Screening",self.screening_from, self.screening_to)

			# Notify HR Manager when status changes to "Joined"
			if previous_status != self.applicant_status and self.applicant_status == "Joined":
				try:
					# Fetch HR Manager's email
					hr_manager_emails = frappe.get_all(
					"User",
					filters={"enabled": 1},
					fields=["email"],
					or_filters={"role_profile_name": "HR Manager"}
					)
					hr_manager_emails = [user["email"] for user in hr_manager_emails if user["email"]]
			
					if hr_manager_emails:
						# Send email notification
						# Prepare context for the email template
						context = {
							"name": self.name,
							"applicant_name": self.applicant_name,
							"title": self.title,
							"url":frappe.utils.get_url_to_form('Job Applicant', self.name),
							"email": self.email_id,
						}

						# Send email using the template
						frappe.sendmail(
							recipients=hr_manager_emails,
							email_template_name="Applicant Joining",
							args=context,
						)
				except Exception as e:
					frappe.log_error(f"Error sending email to HR Manager: {e}", "Applicant Joined Notification Error")
					
			if previous_status != self.applicant_status and self.applicant_status == "Rejected":
				try:
					frappe.sendmail(
						recipients=[self.email_id],
						create_notification_log=True,
						from_users=["Administrator"],
						for_users=["Administrator"],
						args={
							"name": self.applicant_name,
							"title": job_opening_doc.get("job_title"),
						},
						email_template_name="Rejection Email",
					)
				except Exception as e:
					frappe.log_error(f"Error sending email: {e}")

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
			job_opening_status = frappe.db.get_value("Job Opening", self.job_title, "status")
			if job_opening_status == "Closed":
				frappe.throw(
					_("Cannot create a Job Applicant against a closed Job Opening"), title=_("Not Allowed")
				)

	def set_status_for_employee_referral(self):
		emp_ref = frappe.get_doc("Employee Referral", self.employee_referral)
		if self.status in ["Open", "Replied", "Hold"]:
			emp_ref.db_set("status", "In Process")
		elif self.status in ["Accepted", "Rejected"]:
			emp_ref.db_set("status", self.status)


@frappe.whitelist()
def create_interview(doc, interview_round):
	import json

	if isinstance(doc, str):
		doc = json.loads(doc)
		doc = frappe.get_doc(doc)

	round_designation = frappe.db.get_value("Interview Round", interview_round, "designation")

	if round_designation and doc.designation and round_designation != doc.designation:
		frappe.throw(
			_("Interview Round {0} is only applicable for the Designation {1}").format(
				interview_round, round_designation
			)
		)

	interview = frappe.new_doc("Interview")
	interview.interview_round = interview_round
	interview.job_applicant = doc.name
	interview.applicant_name = doc.applicant_name
	interview.designation = doc.designation
	interview.resume_link = doc.resume_link
	interview.job_opening = doc.job_title
	interview.job_title = doc.title

	interviewers = get_interviewers(interview_round)
	for d in interviewers:
		interview.append("interview_details", {"interviewer": d.interviewer})

	return interview


@frappe.whitelist()
def get_interview_details(job_applicant):
	interview_details = frappe.db.get_all(
		"Interview",
		filters={"job_applicant": job_applicant, "docstatus": ["!=", 2]},
		fields=["name", "interview_round", "scheduled_on", "average_rating", "status"],
	)
	interview_detail_map = {}
	meta = frappe.get_meta("Interview")
	number_of_stars = meta.get_options("average_rating") or 5

	for detail in interview_details:
		detail.average_rating = detail.average_rating * number_of_stars if detail.average_rating else 0

		interview_detail_map[detail.name] = detail

	return {"interviews": interview_detail_map, "stars": number_of_stars}


@frappe.whitelist()
def get_applicant_to_hire_percentage():
	total_applicants = frappe.db.count("Job Applicant")
	total_hired = frappe.db.count("Job Applicant", filters={"status": "Accepted"})

	return {
		"value": flt(total_hired) / flt(total_applicants) * 100 if total_applicants else 0,
		"fieldtype": "Percent",
	}


def get_slack_user_id(email):
	system_settings = frappe.get_single("System Settings")
	SLACK_API_URL = "https://slack.com/api/users.lookupByEmail"
	SLACK_TOKEN = system_settings.slack_token

	"""Fetch Slack User ID using the email address."""
	headers = {
		"Authorization": f"Bearer {SLACK_TOKEN}",
		"Content-Type": "application/json"
	}
	response = requests.get(SLACK_API_URL, headers=headers, params={"email": email})
	data = response.json()
	
	if data.get("ok"):
		return data["user"]["id"]
	else:
		print(f"Error fetching Slack user ID for {email}: {data.get('error')}")
		return None

def send_slack_message(emails, applicant_name, job_title, docname, status, screening_from= None, screening_to= None):
	"""
	Loop over the list of emails, fetch each user's Slack ID,
	and send them an individual message.
	"""
	import json
	system_settings = frappe.get_single("System Settings")
	SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
	SLACK_TOKEN = system_settings.slack_token
	
	for email in emails:
		user_id = get_slack_user_id(email)
		doctype = "Job Applicant"
		message = ""
		document_link = frappe.utils.get_url_to_form(doctype, docname)
		if user_id:
			if status == "Lead Screening":
				message = f"Hello <@{user_id}>, please review the CV of {applicant_name} for the position {job_title}.\nCandidate details can be accessed via\n {document_link}."
			if status == "Telephonic Screening":
				message = f"Hello <@{user_id}>, please review the profile of {applicant_name} and conduct a telephonic screening interview for the position {job_title}.\nCandidate details can be accessed via\n {document_link}. \nPlease review within the timeframe of {screening_from} to {screening_to}."
			payload = {
				"channel": user_id,
				"text": message
			}
			headers = {
				"Authorization": f"Bearer {SLACK_TOKEN}",
				"Content-Type": "application/json"
			}
			response = requests.post(SLACK_POST_MESSAGE_URL, headers=headers, data=json.dumps(payload))
			result = response.json()
		else:
			print(f"Could not find Slack user for {email}")

