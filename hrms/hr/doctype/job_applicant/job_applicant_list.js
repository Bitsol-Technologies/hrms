// Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and Contributors
// MIT License. See license.txt

frappe.listview_settings["Job Applicant"] = {
    add_fields: ["applicant_status"],
    get_indicator: function (doc) {
        const initial_statuses = [
            "Applied",
            "HR Screening",
            "Lead Screening",
            "Telephonic Screening",
            "Schedule Test",
            "Test Scheduled",
            "Schedule First Interview",
            "First Interview Scheduled",
            "Schedule Second Interview",
            "Second Interview Scheduled",
            "Assignment Submission",
            "Schedule Final Interview",
            "Final Interview Scheduled",
        ];

        const accepted_statuses = [
            "CV Accepted",
            "Offer Decision & Preparation",
            "Offer Accepted",
            "Background Check",
            "Onboarding",
            "Joined",
        ];

        const rejected_statuses = [
            "CV Rejected",
            "Rejected",
        ];

        if (initial_statuses.includes(doc.applicant_status)) {
            return [__(doc.applicant_status), "orange", "applicant_status,=," + doc.applicant_status];
        } else if (accepted_statuses.includes(doc.applicant_status)) {
            return [__(doc.applicant_status), "green", "applicant_status,=," + doc.applicant_status];
        } else if (rejected_statuses.includes(doc.applicant_status)) {
            return [__(doc.applicant_status), "red", "applicant_status,=," + doc.applicant_status];
        } else {
            return [__(doc.applicant_status), "gray", "applicant_status,=," + doc.applicant_status]; // Default color
        }
    },
};
