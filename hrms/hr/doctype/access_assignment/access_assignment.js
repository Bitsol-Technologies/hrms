// Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Access Assignment", {
	onload: function (frm) {
		// Only set the assigned_by field if the form is new (not saved yet)
		if (frm.is_new()) {
			frm.set_value("assigned_by", frappe.session.user);
		}
	},
	tool: function (frm) {
		// Fetch expiry_days when a tool is selected
		if (frm.doc.tool) {
			frappe.db.get_value(
				"Access Tools",
				frm.doc.tool,
				"default_expiry_period",
				function (value) {
					if (value && value.default_expiry_period) {
						// Calculate the expiry date by adding expiry_days to the current date
						let current_date = frappe.datetime.now_date();
						let expiry_date = frappe.datetime.add_days(
							current_date,
							value.default_expiry_period,
						);

						// Set the expiry date field in the form
						frm.set_value("expiration_date", expiry_date);
					}
				},
			);
		}
	},
	status: function (frm) {
		// When status is set to "Revoked", set current date
		if (frm.doc.status === "Revoked") {
			frm.set_value("revoked_on", frappe.datetime.nowdate());
		}
	},
});
