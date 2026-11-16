import frappe

RESTRICTED_ROLE = "Restricted Training Events"
UNRESTRICTED_ROLE = "HR Manager"


def _is_restricted(user: str) -> bool:
	"""Users holding the restricted role only see the Training Events they attend.

	Administrator and HR Managers always see everything.
	"""
	if user == "Administrator":
		return False

	roles = frappe.get_roles(user)
	return RESTRICTED_ROLE in roles and UNRESTRICTED_ROLE not in roles


def _attendee_emails(doc) -> set[str]:
	# `employee_emails` is a ", " separated string maintained by TrainingEvent.set_employee_emails
	return {email.strip().lower() for email in (doc.get("employee_emails") or "").split(",") if email.strip()}


def te_query(user=None, doctype=None):
	"""permission_query_conditions hook for Training Event."""
	if not user:
		user = frappe.session.user

	if not _is_restricted(user):
		return None

	# escape() quotes the value and doubles the `%` wildcards, which keeps the condition
	# safe whether or not the final query is executed with parameters
	return f"(`tabTraining Event`.employee_emails like {frappe.db.escape(f'%{user}%')})"


def has_te_permission_query(doc, ptype=None, user=None, debug=False):
	"""has_permission hook for Training Event.

	Controller hooks can only deny: returning True defers to the role permissions.
	"""
	if not user:
		user = frappe.session.user

	if not _is_restricted(user):
		return True

	return user.lower() in _attendee_emails(doc)
