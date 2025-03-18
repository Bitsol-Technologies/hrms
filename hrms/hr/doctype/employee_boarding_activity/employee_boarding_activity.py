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
    print(activities)
    for activity in activities:
        if not activity.user:
            print("no user was assingned") 
            continue  # Skip if no user is assigned
        
        print("Reminder email function triggered") 
        # Fetch the parent Employee Onboarding document
        parent_doc = frappe.get_doc("Employee Onboarding", activity.parent)
        
        # Fetch user details
        user_doc = frappe.get_doc("User", activity.user)
        user_first_name = user_doc.first_name or activity.user
        
        # Format the date
        formatted_date = format_datetime(activity.begin_on)

        # Email subject & message
        subject = f"Reminder: Upcoming Onboarding Task for Employee: {parent_doc.applicant_name}"
        message = f"""
            Hello {user_first_name},<br><br>
            This is a friendly reminder about your upcoming onboarding task for employee <strong>{parent_doc.applicant_name}</strong> scheduled for <strong>{formatted_date}</strong>.<br><br>
            <b>Task:</b> {activity.activity_name}<br>
            <b>Date & Time:</b> {formatted_date}<br>
            {activity.description}<br><br>
            Please ensure that all necessary preparations are in place. Let us know if you have any questions or require assistance.<br><br>
            <b>Regards,</b><br>
            HR Team
        """

        # Send email
        frappe.sendmail(
            recipients=[activity.user],
            subject=subject,
            message=message,
        )
        print("Reminder email sent")
