// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Reimbursement", {
	onload: function (frm) {
		if (frm.is_new() && !frm.doc.employee) {
			// default the employee to the one linked to the session user
			frappe.db
				.get_value("Employee", { user_id: frappe.session.user }, "name")
				.then((r) => {
					if (r.message && r.message.name) {
						frm.set_value("employee", r.message.name);
					}
				});
		}
	},
});
