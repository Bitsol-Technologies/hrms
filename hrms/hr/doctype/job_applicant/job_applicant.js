// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

// For license information, please see license.txt

// for communication
cur_frm.email_field = "email_id";

frappe.ui.form.on("Job Applicant", {
	setup: function (frm) {
		frm.make_methods = {
			Employee: () =>
				frappe.model.open_mapped_doc({
					method: "hrms.hr.doctype.job_applicant.job_applicant.make_employee",
					frm: frm,
				}),
		};
	},

	refresh: function (frm) {
		frm.set_query("job_title", function () {
			return {
				filters: {
					status: "Open",
				},
			};
		});
		frm.events.show_resume(frm);
		frm.events.create_custom_buttons(frm);
		frm.events.get_interview_for_dashboard(frm);
		frm.events.set_telephonic_interviewers_query(frm);
		frm.events.load_feedback(frm);

		if (!frm.toolbar.page.wrapper.find(".prev-doc").length) {
			frm.toolbar.make_navigation();
		}
	},

	// Bitsol: "Interview Feedback" tab - all submitted feedback grouped by interview
	load_feedback: function (frm) {
		if (frm.is_new()) return;

		frappe
			.call({
				method: "hrms.hr.doctype.job_applicant.job_applicant.get_feedback_by_round",
				args: { applicant: frm.doc.name },
			})
			.then((r) => {
				frm.feedback = r.message || [];
				frm.events.render_feedback(frm);
			});
	},

	render_feedback: function (frm) {
		const field = frm.fields_dict.custom_interview_feedback_html;
		if (!field) return;

		frappe.require("interview.bundle.js", () => {
			const wrapper = $(field.wrapper);
			const feedback_html = frappe.render_template("grouped_feedback_history", {
				feedback_history: frm.feedback,
			});
			wrapper.empty();
			$(feedback_html).appendTo(wrapper);
		});
	},

	// Bitsol: an employee can be picked only once as telephonic screening interviewer
	set_telephonic_interviewers_query: function (frm) {
		frm.set_query("employee_id", "telephonic_interviewers", function () {
			const selected = (frm.doc.telephonic_interviewers || [])
				.map((row) => row.employee_id)
				.filter(Boolean);
			return {
				filters: {
					name: ["not in", selected],
					status: "Active",
				},
			};
		});
	},

	show_resume: function (frm) {
		frm.toggle_display("resume_preview_html", false);
		frm.toggle_display("open_resume_button", false);
		if (frm.doc.resume_link) {
			const src_url = frappe.utils.escape_html(frm.doc.resume_link);
			if (src_url.endsWith(".pdf")) {
				frm.toggle_display("resume_preview_html", true);
				frm.events.show_pdf_preview(frm, src_url);
			} else {
				frm.toggle_display("open_resume_button", true);
			}
		}
	},
	show_pdf_preview: function (frm, src_url) {
		let $preview = $(`<div class="img_preview" style="padding-bottom:12px;">
			<object style="background:#323639;" width="100%">
				<embed
				style="background:#323639;"
				width="100%"
				height="1000px"
				src="${src_url}" type="application/pdf"
				>
			</object>
			</div>`);
		frm.get_field("resume_preview_html").$wrapper.html($preview);
	},
	open_resume_button: function (frm) {
		window.open(frm.doc.resume_link, "_blank", "noopener");
	},
	create_custom_buttons: function (frm) {
		if (!frm.doc.__islocal) {
			if (frm.doc.status == "Open") {
				frm.add_custom_button(__("Shortlist"), () => {
					frm.set_value("status", "Shortlisted");
					frm.save();
					frm.refresh();
				});
				frm.add_custom_button(__("Reject"), () => {
					frm.set_value("status", "Rejected");
					frm.save();
					frm.refresh();
				});
			}
			if (frm.doc.status !== "Rejected" && frm.doc.status !== "Accepted") {
				frm.add_custom_button(__("Schedule Interview"), () => {
					frm.events.show_schedule_interview_dialog(frm);
				});
			}
		}

		if (!frm.doc.__islocal && frm.doc.status == "Accepted") {
			if (frm.doc.__onload && frm.doc.__onload.employee) {
				frm.add_custom_button(__("Show Employee"), function () {
					frappe.set_route("Form", "Employee", frm.doc.__onload.employee);
				});
			} else {
				frm.add_custom_button(__("Create Employee"), function () {
					frm.make_methods.Employee();
				});
			}

			if (frm.doc.__onload && frm.doc.__onload.job_offer) {
				$('[data-doctype="Employee Onboarding"]').find("button").show();
				$('[data-doctype="Job Offer"]').find("button").hide();
				frm.add_custom_button(__("View Job Offer"), function () {
					frappe.set_route("Form", "Job Offer", frm.doc.__onload.job_offer);
				});
			} else {
				$('[data-doctype="Employee Onboarding"]').find("button").hide();
				$('[data-doctype="Job Offer"]').find("button").show();
				frm.add_custom_button(__("Create Job Offer"), function () {
					frappe.route_options = {
						job_applicant: frm.doc.name,
						applicant_name: frm.doc.applicant_name,
						designation: frm.doc.job_opening || frm.doc.designation,
					};
					frappe.new_doc("Job Offer");
				});
			}
		}

		// Add Employee Onboarding button if applicant_status is 'Joined'
		if (!frm.doc.__islocal && frm.doc.applicant_status === "Joined") {
			frappe.call({
				method: "hrms.hr.doctype.employee_onboarding.employee_onboarding.check_employee_onboarding_exists",
				args: { job_applicant: frm.doc.name },
				callback: function(r) {
					if (!r.message.exists) {
						frm.add_custom_button(
							__("Employee Onboarding"),
							function () {
								// Double-check status before showing dialog
								if (frm.doc.applicant_status === "Joined") {
									show_onboarding_dialog(frm);
								} else {
									frappe.msgprint(__("Applicant status must be 'Joined' to start onboarding."));
								}
							},
							__("Create")
						);
					}
				}
			});
		}
	},

	get_interview_for_dashboard: function (frm) {
		if (frm.doc.__islocal) return;

		$("div").remove(".form-dashboard-section.custom");
		frappe.call({
			method: "hrms.hr.doctype.job_applicant.job_applicant.get_interview_details",
			args: {
				job_applicant: frm.doc.name,
			},
			callback: function (r) {
				if (r.message) {
					frm.events.make_dashboard(frm, r.message);
				}
			},
		});
	},

	make_dashboard: function (frm, message) {
		frm.dashboard.add_section(
			frappe.render_template("job_applicant_dashboard", {
				data: message.interviews,
				number_of_stars: message.stars,
			}),
			__("Interview Summary"),
		);
	},

	show_schedule_interview_dialog: function (frm) {
		// Interview meta is needed for the Bitsol "Location" field of the dialog
		frappe.model.with_doctype("Interview Detail", () =>
			frappe.model.with_doctype("Interview", () => frm.events.show_schedule_dialog(frm)),
		);
	},

	show_schedule_dialog: function (frm) {
		// Bitsol: Interview.location (Onsite / Remote) is mandatory and drives the meeting link,
		// so it has to be asked here because the dialog inserts the Interview directly.
		const location_df = frappe.meta.get_docfield("Interview", "location");
		const location_fields = location_df
			? [
					{
						label: __(location_df.label || "Location"),
						fieldname: "location",
						fieldtype: "Select",
						options: location_df.options,
						reqd: location_df.reqd,
					},
			  ]
			: [];

		let d = new frappe.ui.Dialog({
			title: __("Schedule Interview"),
			fields: [
				{
					label: __("Interview Type"),
					fieldname: "interview_type",
					fieldtype: "Link",
					options: "Interview Type",
					reqd: 1,
					get_query: () => ({
						filters: frm.doc.designation
							? [["designation", "in", [frm.doc.designation, ""]]]
							: [],
					}),
					onchange() {
						const interview_type = d.get_value("interview_type");
						if (!interview_type) return;
						frappe.call({
							method: "hrms.hr.doctype.interview.interview.get_interviewers",
							args: { interview_type },
							callback(r) {
								d.set_value(
									"interviewers",
									(r.message || []).map((i) => ({ interviewer: i.interviewer })),
								);
							},
						});
					},
				},
				{
					label: __("From Time"),
					fieldname: "from_time",
					fieldtype: "Time",
					reqd: 1,
				},
				{ fieldtype: "Column Break" },
				{
					label: __("Scheduled On"),
					fieldname: "scheduled_on",
					fieldtype: "Date",
					reqd: 1,
					default: frappe.datetime.get_today(),
				},
				{
					label: __("To Time"),
					fieldname: "to_time",
					fieldtype: "Time",
					reqd: 1,
				},
				...location_fields,
				{
					fieldtype: "Section Break",
					label: __("Interviewers"),
				},
				{
					fieldname: "interviewers",
					fieldtype: "Table MultiSelect",
					options: "Interview Detail",
				},
			],
			primary_action_label: __("Schedule"),
			primary_action(values) {
				frappe.call({
					method: "hrms.hr.doctype.job_applicant.job_applicant.schedule_interview",
					args: {
						job_applicant: frm.doc.name,
						interview_type: values.interview_type,
						scheduled_on: values.scheduled_on,
						from_time: values.from_time || null,
						to_time: values.to_time || null,
						interviewers: values.interviewers || [],
						location: values.location || null,
					},
					callback(r) {
						if (r.message) {
							frappe.show_alert({
								message: __("Interview scheduled successfully"),
								indicator: "green",
							});
							d.hide();
							frm.events.get_interview_for_dashboard(frm);
						}
					},
				});
			},
		});
		d.show();
	},

	on_update: function(frm) {
		if (frm.doc.applicant_status === "Joined" && !frm.doc.__islocal) {
			frappe.call({
				method: "hrms.hr.doctype.employee_onboarding.employee_onboarding.check_employee_onboarding_exists",
				args: { job_applicant: frm.doc.name },
				callback: function(r) {
					if (!r.message.exists) {
						show_onboarding_dialog(frm);
					}
				}
			});
		}
	},
});

function show_onboarding_dialog(frm) {
	let d = new frappe.ui.Dialog({
		title: __("Start Employee Onboarding"),
		fields: [
			{ label: "Job Applicant", fieldname: "job_applicant", fieldtype: "Link", options: "Job Applicant", reqd: 1, default: frm.doc.name, read_only: 1 },
			{ label: "Company", fieldname: "company", fieldtype: "Link", options: "Company", reqd: 1 },
			{ label: "Date of Joining", fieldname: "date_of_joining", fieldtype: "Date", reqd: 1 },
			{ label: "Holiday List", fieldname: "holiday_list", fieldtype: "Link", options: "Holiday List", reqd: 1 },
			{ label: "Employee Onboarding Template", fieldname: "employee_onboarding_template", fieldtype: "Link", options: "Employee Onboarding Template", reqd: 1 },
		],
		primary_action_label: __("Create Onboarding"),
		primary_action(values) {
			frappe.call({
				method: "hrms.hr.doctype.employee_onboarding.employee_onboarding.create_employee_onboarding_from_applicant",
				args: values,
				callback: function(r) {
					if (r.message) {
						frappe.msgprint(__("Employee Onboarding created: ") + r.message);
						d.hide();
						frappe.set_route("Form", "Employee Onboarding", r.message);
					}
				}
			});
		}
	});
	d.show();
}

frappe.ui.form.on("CV Reviewer", {
	employee_id: function (frm) {
		frm.events.set_telephonic_interviewers_query(frm);
	},
});
