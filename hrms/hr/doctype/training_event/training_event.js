// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Training Event", {
	onload_post_render: function (frm) {
		frm.get_field("employees").grid.set_multiple_add("employee");
	},
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.add_custom_button(__("Training Result"), function () {
				frappe.route_options = {
					training_event: frm.doc.name,
				};
				frappe.set_route("List", "Training Result");
			});
			frm.add_custom_button(__("Training Feedback"), function () {
				frappe.route_options = {
					training_event: frm.doc.name,
				};
				frappe.set_route("List", "Training Feedback");
			});
		}
		frm.events.set_employee_query(frm);
		frm.events.set_bitsol_attendees(frm);
	},

	set_employee_query: function (frm) {
		let emp = [];
		for (let d in frm.doc.employees) {
			if (frm.doc.employees[d].employee) {
				emp.push(frm.doc.employees[d].employee);
			}
		}
		frm.set_query("employee", "employees", function () {
			return {
				filters: {
					name: ["NOT IN", emp],
					status: "Active",
				},
			};
		});
	},
	// Bitsol: add all active employees having one of the selected role profiles and all
	// employees assigned (ToDo) to the Training Program as attendees
	set_bitsol_attendees: function (frm) {
		if (frm.doc.docstatus === 2 || !frm.has_perm("write")) return;

		const role_profiles = (frm.doc.role_profile || []).map((rp) => rp.role_profile).filter(Boolean);
		if (!role_profiles.length && !frm.doc.training_program) return;

		frappe.call({
			method: "hrms.hr.doctype.training_event.training_event_attendees.get_attendees",
			args: {
				role_profiles: role_profiles,
				training_program: frm.doc.training_program || null,
			},
			callback: function (r) {
				const existing_employees = (frm.doc.employees || []).map((row) => row.employee);
				let added = false;

				(r.message || []).forEach((attendee) => {
					if (existing_employees.includes(attendee.employee)) return;

					const row = frm.add_child("employees");
					row.employee = attendee.employee;
					row.employee_name = attendee.employee_name;
					existing_employees.push(attendee.employee);
					added = true;
				});

				if (added) {
					frm.refresh_field("employees");
					frm.events.set_employee_query(frm);
				}
			},
		});
	},
});

frappe.ui.form.on("Training Event Employee", {
	employee: function (frm) {
		frm.events.set_employee_query(frm);
	},
});
