<p>{% set job_opening = frappe.get_doc("Job Opening", doc.job_title) %}</p>

<p>Dear {{doc.applicant_name}},</p>

<p>Thank you for your interest in the <b>{{job_opening.job_title}}</b> role at Bitsol Technologies. Your online application has been received and is under review by our HR Team. If your experience and qualifications correspond to the requirements of this role, a member of the Recruitment Team will contact you for an initial discussion.</p>

<p>We also encourage you to follow us and talk with us on <a href="https://www.linkedin.com/company/bitsoltech/">LinkedIn</a>, <a href="https://www.facebook.com/bitsoltechnologies/">Facebook</a>, <a href="https://x.com/bitsoltech">Twitter</a>, for valuable updates and interactions about careers at Bitsol. To apply to additional positions, please visit our job search page at <a href="https://bitsol.tech/careers/">https://bitsol.tech/careers/</a> and check back often as new opportunities are posted daily.</p>

<p>Thank you,</p>

<p>HR , Bitsol Technologies</p>
