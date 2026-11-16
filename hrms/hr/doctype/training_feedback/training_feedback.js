// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Training Feedback", {
	onload: function (frm) {
		// Bitsol: Training Event.course is a table of courses (Training Event Courses), so it is no
		// longer fetched into Training Feedback.course
		frm.add_fetch("training_event", "event_name", "event_name");
		frm.add_fetch("training_event", "trainer_name", "trainer_name");
	},
});
