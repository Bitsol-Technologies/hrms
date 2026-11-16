frappe.ready(() => {
	// Bitsol: applicants may only upload a file for their resume, so the uploader's
	// "Link" and "Camera" options are hidden. In v16 the FileUploader accepts
	// `allow_web_link` / `allow_take_photo`, so they are switched off through the
	// attach control's upload options instead of hiding the buttons in the DOM.
	const attach_field = frappe.web_form.fields_dict["resume_attachment"];
	if (!attach_field) return;

	const original_set_upload_options = attach_field.set_upload_options;

	attach_field.set_upload_options = function () {
		original_set_upload_options.apply(this, arguments);
		Object.assign(this.upload_options, {
			allow_web_link: false,
			allow_take_photo: false,
		});
	};
});
