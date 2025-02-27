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
		frm.events.set_role_profile_employees(frm);
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
	set_role_profile_employees: function(frm) {
		if (!frm.doc.role_profile || !frm.doc.role_profile.length) return;
		// Map all role profile values from the child table
		let role_profile_names = frm.doc.role_profile
        .map(rp => rp.role_profile)
        .filter(Boolean); // Remove any falsy values
    	if (!role_profile_names.length) return;
		// Step 1: Fetch users with the selected role profile
		frappe.db.get_list("User", {
			filters: [["role_profile_name", "in", role_profile_names]],
			fields: ["name"],
			limit: 500,
		}).then(userResponse => {
			let user_ids = userResponse.map(user => user.name);
			if (!user_ids.length) return;
			// Step 2: Fetch employees whose user_id is in user_ids
			frappe.db.get_list("Employee", {
				filters: [
				["user_id", "in", user_ids],
				["status", "=", "Active"]
				],
				fields: ["name", "employee_name"],
				limit: 500,
			}).then(employeeResponse => {
				let existing_employees = frm.doc.employees.map(emp => emp.employee);
				employeeResponse.forEach(employee => {
					if (!existing_employees.includes(employee.name)) {
						let row = frm.add_child("employees");
						row.employee = employee.name;
						row.employee_name = employee.employee_name;
					}
				});
				frm.refresh_field("employees");
			});
		});
	}
	
	
});

frappe.ui.form.on("Training Event Employee", {
	employee: function (frm) {
		frm.events.set_employee_query(frm);
	},
});
