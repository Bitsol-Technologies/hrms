# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe


@frappe.whitelist()
def get_attendees(
	role_profiles: str | list | None = None, training_program: str | None = None
) -> list[dict]:
	"""Return the employees that should attend a Training Event.

	- active employees whose user has one of ``role_profiles``
	- employees assigned (open ToDo) to ``training_program``

	Looked up server side because User role profiles (permlevel 1) and other users' ToDos
	are not readable by the HR users who maintain Training Events.
	"""
	frappe.has_permission("Training Event", "write", throw=True)

	if isinstance(role_profiles, str):
		role_profiles = frappe.parse_json(role_profiles)
	role_profiles = [rp for rp in (role_profiles or []) if rp]

	employees: dict[str, dict] = {}

	if role_profiles:
		users = set(
			frappe.get_all(
				"User Role Profile",
				filters={"parenttype": "User", "role_profile": ["in", role_profiles]},
				pluck="parent",
			)
		)
		# deprecated single role profile field, still populated on older records
		users.update(
			frappe.get_all("User", filters={"role_profile_name": ["in", role_profiles]}, pluck="name")
		)
		if users:
			for employee in frappe.get_all(
				"Employee",
				filters={"user_id": ["in", list(users)], "status": "Active"},
				fields=["name", "employee_name"],
				order_by="name",
			):
				employees.setdefault(employee.name, employee)

	if training_program:
		trainees = set(
			frappe.get_all(
				"ToDo",
				filters={
					"reference_type": "Training Program",
					"reference_name": training_program,
					"status": ["not in", ["Cancelled", "Closed"]],
				},
				pluck="allocated_to",
			)
		)
		trainees.discard(None)
		if trainees:
			for employee in frappe.get_all(
				"Employee",
				filters={"user_id": ["in", list(trainees)]},
				fields=["name", "employee_name"],
				order_by="name",
			):
				employees.setdefault(employee.name, employee)

	return [{"employee": d.name, "employee_name": d.employee_name} for d in employees.values()]
