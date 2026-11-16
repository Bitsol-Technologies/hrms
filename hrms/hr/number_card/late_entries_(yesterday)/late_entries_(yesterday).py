import frappe
from frappe.utils import add_days, getdate


@frappe.whitelist()
def get_late_entries() -> dict:
	"""Custom Number Card source: late entries of the previous working day.

	On Mondays the card looks back to Friday, otherwise to yesterday.
	"""
	today = getdate()
	# Monday -> last Friday, any other day -> yesterday
	target_date = add_days(today, -3 if today.weekday() == 0 else -1)
	target_date_str = target_date.strftime("%Y-%m-%d")

	late_entries_count = frappe.db.count(
		"Attendance",
		filters={"attendance_date": target_date_str, "late_entry": 1},
	)

	return {
		"value": late_entries_count,
		"fieldtype": "Int",
		"route_options": {
			"attendance_date": ["=", target_date_str],
			"late_entry": ["=", 1],
		},
		"route": ["List", "Attendance"],
	}
