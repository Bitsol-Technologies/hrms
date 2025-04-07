import frappe
from frappe.model.document import Document
from frappe.utils import format_datetime, today, getdate

class EmployeeBoardingActivity(Document):
	pass

def send_onboarding_reminder():
	"""Sends a reminder email to assignees for onboarding tasks scheduled for today."""
	# Get all Employee Boarding Activities scheduled for today
	# Convert today's date to a string in the format YYYY-MM-DD
	today_date_str = getdate(today()).strftime('%Y-%m-%d')

	activities = frappe.db.sql("""
		SELECT name, user, activity_name, begin_on, description, parent
		FROM `tabEmployee Boarding Activity`
		WHERE DATE(begin_on) = %s
	""", (today_date_str,), as_dict=True)
	for activity in activities:
		if not activity.user:
			continue  # Skip if no user is assigned
		
		# Fetch the parent Employee Onboarding document
		parent_doc = frappe.get_doc("Employee Onboarding", activity.parent)
		
		# Fetch user details
		user_doc = frappe.get_doc("User", activity.user)
		user_first_name = user_doc.first_name or activity.user
		
		# Format the date
		formatted_date = format_datetime(activity.begin_on)

		# Prepare context for the email template
		context = {
			"user_first_name": user_first_name,
			"applicant_name": parent_doc.applicant_name,
			"formatted_date": formatted_date,
			"activity_name": activity.activity_name,
			"description": activity.description,
		}

		# Send email using the template
		frappe.sendmail(
			recipients=[activity.user],
			email_template_name="Onboarding Reminder",
			args=context,
		)
