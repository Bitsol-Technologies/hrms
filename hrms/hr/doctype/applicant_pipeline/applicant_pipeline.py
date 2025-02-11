# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import requests
import json

class ApplicantPipeline(Document):
	def save(self):
          job_opening_doc = frappe.get_doc("Job Opening", self.job_title)
          applicant_data = frappe.db.get_value("Job Applicant", self.applicant_name, ["applicant_name", "resume_link"], as_dict=True)
          leads = [lead.user for lead in job_opening_doc.leads]
          if self.status == "Lead Review":
               send_slack_message(leads, applicant_data.get("applicant_name"), applicant_data.get("resume_link"))
          if self.status == "First Interview":
               frappe.sendmail(
                         recipients=[self.applicant_name],
                         create_notification_log=True,
                         from_users=["Administrator"],
                         args={
                              "name": applicant_data.get("applicant_name"),
                              "title": job_opening_doc.get("job_title"),
                         },
                         email_template_name="Offer Letter",
                    )
          if self.status == "Second Interview":
               frappe.sendmail(
                         recipients=[self.applicant_name],
                         create_notification_log=True,
                         from_users=["Administrator"],
                         args={
                              "name": applicant_data.get("applicant_name"),
                              "title": job_opening_doc.get("job_title"),
                         },
                         email_template_name="Offer Letter",
                    )
          if self.status == "Offer Decision":
               frappe.sendmail(
                         recipients=[self.applicant_name],
                         create_notification_log=True,
                         from_users=["Administrator"],
                         args={
                              "name": applicant_data.get("applicant_name"),
                              "title": job_opening_doc.get("job_title"),
                         },
                         email_template_name="Offer Letter",
                    )
          if self.status == "Offer Acceptance":
               frappe.sendmail(
                         recipients=[self.applicant_name],
                         create_notification_log=True,
                         from_users=["Administrator"],
                         args={
                              "name": applicant_data.get("applicant_name"),
                              "title": job_opening_doc.get("job_title"),
                         },
                         email_template_name="Offer Acceptance",
                    )
          if self.status == "Rejected":
               try:
                    frappe.sendmail(
                         recipients=[self.applicant_name],
                         create_notification_log=True,
                         from_users=["Administrator"],
                         args={
                              "name": applicant_data.get("applicant_name"),
                              "title": job_opening_doc.get("job_title"),
                         },
                         email_template_name="Rejection Email",
                    )
               except Exception as e:
                    frappe.log_error(f"Error sending email: {e}")
          super().save()
          

def get_slack_user_id(email):
    system_settings = frappe.get_single("System Settings")
    SLACK_API_URL = system_settings.slack_api_url
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

def send_slack_message(emails, applicant_name, resume_link):
    """
    Loop over the list of emails, fetch each user's Slack ID,
    and send them an individual message.
    """
    system_settings = frappe.get_single("System Settings")

    SLACK_TOKEN = system_settings.slack_token

    SLACK_POST_MESSAGE_URL = system_settings.slack_post_message_url
    
    for email in emails:
        user_id = get_slack_user_id(email)
        if user_id:
            message = f"Hello <@{user_id}>, please review the CV of {applicant_name}.\nResume Link: {resume_link}"
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

