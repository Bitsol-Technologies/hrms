// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.job_offer");

frappe.ui.form.on("Job Offer", {
	onload: function (frm) {
		frm.set_query("select_terms", function () {
			return { filters: { hr: 1 } };
		});
	},

	setup: function (frm) {
		frm.email_field = "applicant_email";
	},

	select_terms: function (frm) {
		erpnext.utils.get_terms(frm.doc.select_terms, frm.doc, function (r) {
			if (!r.exc) {
				frm.set_value("terms", r.message);
			}
		});
	},
	job_offer_term_template: function (frm) {
		if (!frm.doc.job_offer_term_template) return;

		frappe.db
			.get_doc("Job Offer Term Template", frm.doc.job_offer_term_template)
			.then((doc) => {
				frm.clear_table("offer_terms");
				doc.offer_terms.forEach((term) => {
					frm.add_child("offer_terms", term);
				});
				refresh_field("offer_terms");
			});
	},

	refresh: function (frm) {
		if (
			!frm.doc.__islocal &&
			frm.doc.status == "Accepted" &&
			frm.doc.docstatus === 1 &&
			(!frm.doc.__onload || !frm.doc.__onload.employee)
		) {
			frm.add_custom_button(__("Create Employee"), function () {
				erpnext.job_offer.make_employee(frm);
			});
		}

		if (frm.doc.__onload && frm.doc.__onload.employee) {
			frm.add_custom_button(__("Show Employee"), function () {
				frappe.set_route("Form", "Employee", frm.doc.__onload.employee);
			});
		}
		if (!frm.doc.__islocal && frm.doc.status === "Accepted" && !frm.doc.offer_email_sent) {
            frm.add_custom_button(__("Send Offer Letter Email"), function () {
                // Build the default recipients string
                let defaultRecipients = frm.doc.applicant_email + ", mashal@bitsol.tech, rizwan@bitsol.tech, javeed@bitsol.tech";
                // Create a dialog to show default recipients and allow additional ones
                let d = new frappe.ui.Dialog({
                    title: __("Confirm Offer Email Recipients"),
                    fields: [
                        {
                            fieldname: "default_recipients",
                            fieldtype: "Read Only",
                            label: __("Default Recipients"),
                            default: defaultRecipients
                        },
						{
                            fieldname: "additional_recipients",
                            fieldtype: "MultiSelectPills",
                            label: __("Additional Recipients"),
                            reqd: false,
                            get_data: function (txt) {
                                return frappe.db.get_link_options("User", txt, { user_type: "System User" });
                            }
                        }
						
                    ],
                    primary_action_label: __("Send Email"),
                    primary_action(values) {
                        // Combine default recipients with any additional recipients entered
                        let recipients = defaultRecipients;
                        if (values.additional_recipients && values.additional_recipients.length) {
                            recipients += ", " + values.additional_recipients.join(", ");
                        }
                        // Call the server method to send the email
                        frappe.call({
                            method: "hrms.hr.doctype.job_offer.job_offer.send_offer_letter",
                            args: {
                                docname: frm.doc.name,
                                recipients: recipients
                            },
                            callback: function (r) {
                                if (!r.exc) {
                                    frappe.msgprint(__("Offer letter email sent successfully."));
									frm.reload_doc();
                                    d.hide();
                                }
                            }
                        });
                    }
                });
                d.show();
            });
		}
	},
});

erpnext.job_offer.make_employee = function (frm) {
	frappe.model.open_mapped_doc({
		method: "hrms.hr.doctype.job_offer.job_offer.make_employee",
		frm: frm,
	});
};
