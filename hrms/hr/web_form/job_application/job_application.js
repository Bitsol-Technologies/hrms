frappe.ready(() => {
    const attach_field = frappe.web_form.fields_dict["resume_attachment"];
    if (!attach_field) return;

    const original_on_attach_click = attach_field.on_attach_click;

    attach_field.on_attach_click = function() {
        // Call the original on_attach_click first
        if (original_on_attach_click) {
            original_on_attach_click.apply(this, arguments);
        } else {
            // If there's no original, at least initialize the uploader
            this.set_upload_options();
            this.file_uploader = new frappe.ui.FileUploader(this.upload_options);
            this.file_uploader.dialog.show();
        }

        // Then, after a delay, try to hide the camera and link buttons
        const dialog = this.file_uploader ? this.file_uploader.dialog : null;
        if (dialog) {
            setTimeout(() => {
                if (dialog && dialog.$wrapper) {
                    const cameraButton = dialog.$wrapper.find('button:contains("Camera")').closest('.btn-file-upload');
                    if (cameraButton.length) {
                        cameraButton.hide();
                    }

                    const linkButton = dialog.$wrapper.find('button:contains("Link")').closest('.btn-file-upload');
                    if (linkButton.length) {
                        linkButton.hide();
                    }
                }
            }, 250);
        }
    };
});