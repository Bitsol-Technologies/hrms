// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Work From Home", {
	after_save(frm) {
		// Share the document with the approver once it has been requested
		if (frm.doc.status === "Requested" && frm.doc.approver_id) {
			frm.call({
				method: "frappe.share.add",
				args: {
					doctype: "Work From Home",
					name: frm.doc.name,
					user: frm.doc.approver_id,
					read: 1,
					write: 1,
					submit: 1,
					share: 1,
					everyone: 0,
					notify: 1,
				},
			});
		}
	},

	onload(frm) {
		if (!frm.doc.employee) {
			// default the employee to the logged-in user's employee record
			frappe.db.get_value("Employee", { user_id: frappe.session.user }, "name").then((r) => {
				if (r.message?.name && !frm.doc.employee) {
					frm.set_value("employee", r.message.name);
				}
			});
		}
	},

	validate(frm) {
		if (frm.doc.from_date === frm.doc.to_date && cint(frm.doc.half_day)) {
			frm.doc.half_day_date = frm.doc.from_date;
		} else if (frm.doc.half_day === 0) {
			frm.doc.half_day_date = "";
		}
		frm.toggle_reqd("half_day_date", cint(frm.doc.half_day));
	},

	to_date_datepicker(frm) {
		const to_date_datepicker = frm.fields_dict.to_date.datepicker;
		if (!to_date_datepicker || !frm.doc.from_date) return;

		to_date_datepicker.update({
			minDate: frappe.datetime.str_to_obj(frm.doc.from_date),
		});
	},

	half_day_datepicker(frm) {
		frm.set_value("half_day_date", "");
		if (!(frm.doc.half_day && frm.doc.from_date && frm.doc.to_date)) return;

		const half_day_datepicker = frm.fields_dict.half_day_date.datepicker;
		if (!half_day_datepicker) return;

		half_day_datepicker.update({
			minDate: frappe.datetime.str_to_obj(frm.doc.from_date),
			maxDate: frappe.datetime.str_to_obj(frm.doc.to_date),
		});
	},

	// Trigger to calculate total days when from_date changes
	from_date(frm) {
		frm.trigger("calculate_total_days");
		frm.trigger("half_day_datepicker");
		frm.trigger("to_date_datepicker");
	},

	// Trigger to calculate total days when to_date changes
	to_date(frm) {
		frm.trigger("calculate_total_days");
		frm.trigger("half_day_datepicker");
	},

	half_day_date(frm) {
		frm.trigger("calculate_total_days");
	},

	// Trigger to calculate total days when half_day is toggled
	half_day(frm) {
		if (frm.doc.half_day) {
			if (frm.doc.from_date == frm.doc.to_date) {
				frm.set_value("half_day_date", frm.doc.from_date);
			} else {
				frm.trigger("half_day_datepicker");
			}
		} else {
			frm.set_value("half_day_date", "");
		}
		frm.trigger("calculate_total_days");
	},

	// Function to calculate total days
	calculate_total_days(frm) {
		if (frm.doc.from_date && frm.doc.to_date && frm.doc.employee) {
			// Server call to calculate total WFH days excluding holidays, considering half days
			return frappe.call({
				method: "hrms.hr.doctype.work_from_home.work_from_home.get_number_of_wfh_days",
				args: {
					employee: frm.doc.employee,
					from_date: frm.doc.from_date,
					to_date: frm.doc.to_date,
					half_day: frm.doc.half_day,
					half_day_date: frm.doc.half_day_date,
				},
				callback: function (r) {
					if (r && r.message) {
						frm.set_value("total_days", r.message);
					}
				},
			});
		}
	},
});
