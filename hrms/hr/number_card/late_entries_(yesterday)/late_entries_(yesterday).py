import frappe
from datetime import datetime, timedelta

@frappe.whitelist()
def get_late_entries():
    # Get today's date
    today = datetime.today()
    # Check if today is Monday
    if today.weekday() == 0:  # Monday
        # If today is Monday, get the date for last Friday
        target_date = today - timedelta(days=3)
    else:
        # Otherwise, get yesterday's date
        target_date = today - timedelta(days=1)
    
    # Format the target date to match the date format in the Attendance doctype
    target_date_str = target_date.strftime('%Y-%m-%d')
    # Query the Attendance doctype for late entries on the target date
    late_entries_count = frappe.db.count('Attendance', filters={
        'attendance_date': target_date_str,
        'late_entry': 1
    })

    return {
        "value": late_entries_count,
        "fieldtype": "Int",
        "route_options": {
        "attendance_date": ["=", target_date_str],  # Filter Attendance by target date
        "late_entry": ["=", 1]  # Only show late entries
        },
        "route": ["List", "Attendance"]  # Redirect to Attendance List
        }