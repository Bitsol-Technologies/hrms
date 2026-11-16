import frappe


def get_context(context):
	"""Expose the HD Ticket requester's e-mail so the recipient row (cc: {{ ticket_raised_by }})
	can address the person who raised the service request. HD Ticket Comment itself has no
	e-mail field pointing at the requester, so it must be looked up from the linked ticket."""
	doc = context.get("doc")
	ticket = doc.get("reference_ticket") if doc else None
	if not ticket:
		return {}

	return {"ticket_raised_by": frappe.db.get_value("HD Ticket", ticket, "raised_by") or ""}
