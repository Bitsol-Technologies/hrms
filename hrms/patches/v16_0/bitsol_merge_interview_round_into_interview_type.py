"""Bitsol: carry Interview Round data into Interview Type for sites upgrading from version-15.

Upstream v16 merged Interview Round into Interview Type (designation, expected skill set,
expected average rating and interviewers now live on Interview Type, and Interview /
Interview Feedback link to `interview_type`). Upstream's own patch
(`merge_interview_round_with_interview_type`) is commented out in v16 and only renames
documents, so the round data is lost on upgrade.

This patch keeps Bitsol's v15 data:
- every Interview Round becomes an Interview Type with the same name (the round's linked
  Interview Type is renamed when only that round uses it, otherwise a new one is created);
- empty fields of that Interview Type are filled from the round: designation, expected
  average rating (Bitsol's half-point Select '0'..'5'), expected skill set rows and
  interviewers (merged, without duplicates);
- `Interview.interview_type` and `Interview Feedback.interview_type` are set from the
  orphaned `interview_round` column where they are still empty.

It is idempotent and does nothing on sites that never had Interview Round.
"""

import frappe
from frappe.model.rename_doc import rename_doc
from frappe.utils import cstr, flt

RATING_OPTIONS = ("0", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5")


def execute():
	if not frappe.db.has_table("Interview Round"):
		return

	rounds = frappe.db.sql(
		"select name, interview_type, designation, expected_average_rating from `tabInterview Round`",
		as_dict=True,
	)
	if not rounds:
		return

	round_names = {row.name for row in rounds}
	type_usage = {}
	for row in rounds:
		if row.interview_type:
			type_usage[row.interview_type] = type_usage.get(row.interview_type, 0) + 1

	for row in rounds:
		interview_type = get_or_create_interview_type(row, type_usage, round_names)
		if interview_type:
			copy_round_data(row, interview_type)

	set_interview_type_from_round("Interview")
	set_interview_type_from_round("Interview Feedback")


def get_or_create_interview_type(row, type_usage: dict, round_names: set) -> str | None:
	round_name = row.name
	if not round_name:
		return None

	if frappe.db.exists("Interview Type", round_name):
		return round_name

	linked_type = row.interview_type
	if (
		linked_type
		and type_usage.get(linked_type) == 1
		and frappe.db.exists("Interview Type", linked_type)
		# do not rename a type that another round is about to claim by name
		and linked_type not in round_names
	):
		rename_doc("Interview Type", linked_type, round_name, force=True, show_alert=False)
		frappe.db.set_value("Interview Type", round_name, "interview_type_name", round_name)
		return round_name

	doc = frappe.new_doc("Interview Type")
	doc.interview_type_name = round_name
	if linked_type:
		doc.description = frappe.db.get_value("Interview Type", linked_type, "description")
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.insert(ignore_permissions=True)
	return doc.name


def copy_round_data(row, interview_type: str):
	doc = frappe.get_doc("Interview Type", interview_type)

	if not doc.interview_type_name:
		doc.interview_type_name = doc.name

	if not doc.designation and row.designation:
		doc.designation = row.designation

	if not cstr(doc.expected_average_rating).strip():
		rating = normalise_rating(row.expected_average_rating)
		if rating is not None:
			doc.expected_average_rating = rating

	if not doc.expected_skill_set:
		for skill in frappe.get_all(
			"Expected Skill Set",
			filters={"parenttype": "Interview Round", "parent": row.name},
			fields=["skill", "description"],
			order_by="idx",
		):
			doc.append("expected_skill_set", {"skill": skill.skill, "description": skill.description})

	existing_users = {d.user for d in doc.interviewers}
	for user in frappe.get_all(
		"Interviewer",
		filters={"parenttype": "Interview Round", "parent": row.name},
		pluck="user",
		order_by="idx",
	):
		if user and user not in existing_users:
			doc.append("interviewers", {"user": user})
			existing_users.add(user)

	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.save(ignore_permissions=True)


def normalise_rating(value) -> str | None:
	"""Map a stored rating onto Interview Type's half-point Select options ('0'..'5')."""
	value = cstr(value).strip()
	if not value:
		return None
	if value in RATING_OPTIONS:
		return value

	number = flt(value)
	# round to the nearest half point, as the Select only offers half points
	number = round(number * 2) / 2
	text = str(int(number)) if number == int(number) else str(number)
	return text if text in RATING_OPTIONS else None


def set_interview_type_from_round(doctype: str):
	if not frappe.db.has_column(doctype, "interview_round") or not frappe.db.has_column(
		doctype, "interview_type"
	):
		return

	table = frappe.qb.DocType(doctype)
	(
		frappe.qb.update(table)
		.set(table.interview_type, table.interview_round)
		.where(
			(table.interview_round.isnotnull())
			& (table.interview_round != "")
			& ((table.interview_type.isnull()) | (table.interview_type == ""))
		)
	).run()
