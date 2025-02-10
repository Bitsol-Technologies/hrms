# get all documents in the access_assignment doctype which are expired by status

import frappe


def get_expired_access_assignments():
	return frappe.get_all("Access Assignment", filters={"status": "Expired"}, fields=["*"])


# make a function that will add an alert to the user's dashboard


def add_access_expiry_alert():
	access_assignments = get_expired_access_assignments()
	print(access_assignments)
	for access_assignment in access_assignments:
		# get me a list of users who have access to this access assignment document fro fileds risk_owner, user

		users = [access_assignment.risk_owner, access_assignment.user]
		print(users)
		for user in users:
			doc = frappe.get_doc(
				{
					"doctype": "Note",
					"title": "Access Expiry Notification",
					"content": f"Access to {access_assignment.tool_name} for user {access_assignment.employee_name} against project {access_assignment.project_name} has expired. Please contact the admin to renew it.",
					"public": 0,  # Private note (only visible to the assigned user
					"owner": user,  # The user who should see the note
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value("Note", doc.name, {"owner": user})
