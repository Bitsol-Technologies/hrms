frappe.ready(() => {
	// Your code here
	const f = frappe.web_form.fields_dict["resume_attachment"];
	if (!f) return;
  
	f.on_attach_click = function() {
		const uploadOptions = f.get_upload_options ? f.get_upload_options() : {};
		uploadOptions.disable_file_browser = true;
  
  
		this.file_uploader = new frappe.ui.FileUploader(uploadOptions);
		const dialog = this.file_uploader.dialog;
		dialog.show();
  
		setTimeout(() => {
		  if (dialog && dialog.$wrapper) {
			// Hide Camera Button
			const cameraButton = dialog.$wrapper.find('button:contains("Camera")').closest('.btn-file-upload');
			if (cameraButton.length) {
			  cameraButton.hide();
			} 
  
			// Hide Link Button
			const linkButton = dialog.$wrapper.find('button:contains("Link")').closest('.btn-file-upload');
			if (linkButton.length) {
			  linkButton.hide();
			} 
		  } 
		}, 250);
	  };
  });