// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("HR Notifications", {
	type(frm) {
        if (frm.is_new()) {
			if (frm.doc.type && frm.doc.type !== "Other") {
				frappe.db.get_doc("Email Template", frm.doc.type).then((template) => {
					if (template) {
						frm.set_value("subject", template.subject);
						frm.set_value("message", template.response_html);
					}
				});
			} else {
				frm.set_value("subject", "");
				frm.set_value("message", "");
			}
		}
	},
});
