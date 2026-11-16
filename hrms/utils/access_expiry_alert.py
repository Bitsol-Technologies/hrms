# Bitsol: helper that raises a private Note for every expired Access Assignment.
# It is intentionally NOT wired to the scheduler (same as on version-15); call it manually
# or add it to scheduler_events in hooks.py if the behaviour is wanted.

import frappe


def get_expired_access_assignments():
	return frappe.get_all(
		"Access Assignment",
		filters={"status": "Expired"},
		fields=["name", "risk_owner", "user", "tool_name", "employee_name", "project_name"],
	)


def add_access_expiry_alert():
	for access_assignment in get_expired_access_assignments():
		# the people who should see the note: risk owner and the user holding the access
		users = {access_assignment.risk_owner, access_assignment.user}
		for user in filter(None, users):
			doc = frappe.get_doc(
				{
					"doctype": "Note",
					"title": "Access Expiry Notification",
					"content": (
						f"Access to {access_assignment.tool_name} for user {access_assignment.employee_name} "
						f"against project {access_assignment.project_name} has expired. "
						"Please contact the admin to renew it."
					),
					"public": 0,  # private note, only visible to its owner
				}
			).insert(ignore_permissions=True)
			# insert() always stamps the session user as owner, so re-point it afterwards
			frappe.db.set_value("Note", doc.name, "owner", user)
