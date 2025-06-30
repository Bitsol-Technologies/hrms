# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import get_url_to_form
from bs4 import BeautifulSoup

class Reimbursement(Document):
	def before_save(self):
		self.total_amount = 0
		for expense in self.get("expense_details"):
			self.total_amount += expense.cost

		if self.is_new():
			self.send_reimbursement_email(event="New")

	def validate(self):
		self.ensure_team_lead_approves_non_medical_first()

	def ensure_team_lead_approves_non_medical_first(self):
		if (
		self.reimbursement_type != "Medical"
		and self.docstatus == 0
		and not self.is_new()
	):
			old_doc = frappe.get_doc(self.doctype, self.name)
			if old_doc.status == "Open" and self.status != "Open":
				employee_doc = frappe.get_doc("Employee", self.employee)
				team_lead = employee_doc.team_lead

				if self.custom_employee_user == frappe.session.user:
					return

				if not team_lead or team_lead == frappe.session.user:
					return

				frappe.throw("Only the Team Lead can change the status from 'Open'.")


	def on_submit(self):
		if self.status == "Open":
			frappe.throw("Please approve or reject the reimbursement before submitting.")

		if self.status == "Approved":
			if self.reimbursement_type == "Medical":
				claim_employee = frappe.get_doc("Employee", self.employee)
				if self.total_amount <= claim_employee.medical_balance:
					claim_employee.set("medical_availed", self.total_amount + claim_employee.medical_availed)
					claim_employee.set(
						"medical_balance", claim_employee.medical_allowance - claim_employee.medical_availed
					)
					claim_employee.db_update()
				else:
					frappe.throw("Employee does not have enough balance to claim medical allowance")
		
		self.send_reimbursement_email(event=self.status)

	def on_cancel(self):
		if self.reimbursement_type == "Medical":
			claim_employee = frappe.get_doc("Employee", self.employee)
			claim_employee.set("medical_availed", claim_employee.medical_availed - self.total_amount)
			claim_employee.set(
				"medical_balance", claim_employee.medical_allowance - claim_employee.medical_availed
			)
			claim_employee.db_update()
			frappe.msgprint("Balance of %d added back to the employee medical balance" % (self.total_amount))

	def send_reimbursement_email(self, event):
		employee_doc = frappe.get_doc("Employee", self.employee)
		recipients = []

		if employee_doc.user_id:
			employee_email = frappe.get_value("User", employee_doc.user_id, "email")
			if employee_email:
				recipients.append(employee_email)

		hr_managers = frappe.get_all(
			"User", filters={"enabled": 1, "role_profile_name": "HR"}, pluck="email"
		)
		recipients.extend(hr_managers)

		if self.reimbursement_type != "Medical":
			if employee_doc.team_lead:
				recipients.append(employee_doc.team_lead)

		if not recipients:
			return

		recipients = list(set(recipients))
		template_name = "Reimbursement Status"

		if not frappe.db.exists("Email Template", template_name):
			frappe.log_error(f"Email Template '{template_name}' not found for Reimbursement {self.name}", "Reimbursement Email Error")
			return

		email_template = frappe.get_doc("Email Template", template_name)
		context = self.as_dict()
		context["doc"] = self
		context["reimbursement_url"] = get_url_to_form("Reimbursement", self.name)

		subject = frappe.render_template(str(email_template.subject), context)
		html_content = frappe.render_template(str(email_template.response), context)

		frappe.sendmail(
			recipients=recipients,
			email_template_name=template_name,
			args=context,
			now= True,
		)
		
		insert_hr_notification(subject, html_content, recipients)


def custom_html_to_plaintext(html):
	if not html:
		return ""

	soup = BeautifulSoup(html, "html.parser")
	lines = []

	# Extract the link URL first
	link_tag = soup.find('a')
	link_url = link_tag['href'] if link_tag and link_tag.has_attr('href') else ""

	# Get text from the first <p> tag, which acts as the heading in the template
	heading_p = soup.find('p')
	if heading_p:
		lines.append(heading_p.get_text(strip=True))
		lines.append("") # Add a blank line

	# Process the table for clean key-value output
	table = soup.find('table')
	if table:
		for row in table.find_all('tr'):
			cols = [ele.get_text(strip=True) for ele in row.find_all('td')]
			if len(cols) == 2:
				lines.append(f"{cols[0]}: {cols[1]}")
	
	if table:
		lines.append("") # Add a blank line after the table

	# Add the link text and URL at the end
	if link_tag:
		lines.append(f"{link_tag.get_text(strip=True)}: {link_url}")

	return "\n".join(lines)


def insert_hr_notification(subject, html_content, recipients):
	try:
		message = custom_html_to_plaintext(html_content)
		notification = frappe.get_doc({
			"doctype": "HR Notifications",
			"type": "Other",
			"subject": subject,
			"message": message,
			"send_email": 0,
			"send_slack": 0,
			"send_push": 1,
			"user": [{"user": user_email} for user_email in recipients],
		}).insert(ignore_permissions=True)
		
		frappe.db.set_value("HR Notifications", notification.name, "send_email", 1)
		frappe.db.set_value("HR Notifications", notification.name, "send_push", 0)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Insert HR Notification Error")
