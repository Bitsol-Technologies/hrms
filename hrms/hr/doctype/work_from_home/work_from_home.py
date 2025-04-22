# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
# import json
import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt



@frappe.whitelist()
def get_number_of_wfh_days(
	employee: str,
	from_date: datetime.date,
	to_date: datetime.date,
	half_day: int | str | None = None,
	half_day_date: datetime.date | str | None = None,
	holiday_list: str | None = None,
) -> float:
	from frappe.utils import add_days, cint, date_diff, getdate

	"""Returns number of leave days between 2 dates after considering half day and holidays
	(Based on the include_holiday setting in Leave Type)"""
	number_of_days = 0
	if cint(half_day) == 1:
		if getdate(from_date) == getdate(to_date):
			number_of_days = 0.5
		elif half_day_date and getdate(from_date) <= getdate(half_day_date) <= getdate(to_date):
			number_of_days = date_diff(to_date, from_date) + 0.5
		else:
			number_of_days = date_diff(to_date, from_date) + 1
	else:
		number_of_days = date_diff(to_date, from_date) + 1
	from hrms.hr.doctype.leave_application.leave_application import get_holidays
	number_of_days = flt(number_of_days) - flt(
		get_holidays(employee, from_date, to_date, holiday_list=holiday_list)
	)
	if number_of_days == 0:
		frappe.throw(_("The day(s) on which you are applying for work from home are holidays."))
	return number_of_days


class WorkFromHome(Document):
	def validate(self):
		self.validate_same_day_wfh()
		self.validate_leave_on_same_day()
		self.half_day_wfh_scenarios()

	def validate_same_day_wfh(self):
		"""Validate if a WFH already exists for the same employee on the same date."""

		# Check if there is any overlapping WFH request for the same employee
		existing_wfh = frappe.db.exists(
			"Work From Home",
			{
				"employee": self.employee,
				"from_date": ["<=", self.to_date],
				"to_date": [">=", self.from_date],
				"docstatus": ["!=", 2],  # Exclude cancelled records
				"name": ["!=", self.name],  # Exclude the current WFH record
			},
		)

		if existing_wfh:
			frappe.throw(
				_("A Work From Home entry already exists for this employee on the selected date(s).")
			)

	def validate_leave_on_same_day(self):
		conflicting_leaves = frappe.db.sql(
			"""
								SELECT name FROM `tabLeave Application`
								WHERE employee = %s AND docstatus = 1
								AND ((from_date >= %s AND to_date <= %s)  # leave overlaps with WFH
								OR (from_date = %s AND half_day = 1))
							""",
			(self.employee, self.from_date, self.to_date, self.from_date),
		)
		if conflicting_leaves:
			frappe.throw(_("Cannot apply for WFH as there is an existing leave on the same date."))

	def half_day_wfh_scenarios(self):
		if not self.half_day:
			conflicting_leaves = frappe.db.sql(
				"""
						SELECT name FROM `tabLeave Application`
						WHERE employee = %s AND docstatus = 1
						AND ((from_date <= %s AND to_date >= %s)  # leave overlaps with WFH
						OR (from_date = %s AND half_day = 1))     # half-day leave on WFH day
					""",
				(self.employee, self.from_date, self.to_date, self.from_date),
			)
			if conflicting_leaves:
				frappe.throw(
					_(
						"Cannot apply for full-day WFH as there is an existing full-day leave on the same date."
					)
				)

	def before_save(self):
		if self.status == "Approved" and self.wfh_limit_reached():
			frappe.msgprint(
				"Warning: This user has reached or exceeded the 5 days WFH limit for this month",
				indicator="orange",
				title="WFH Limit Reached",
			)

	def wfh_limit_reached(self):
		total_wfh_days = 0
		if self.employee and self.from_date and self.to_date:
			first_day_of_month = frappe.utils.get_first_day(self.from_date)
			last_day_of_month = frappe.utils.get_last_day(self.from_date)
			total_wfh_days = frappe.db.sql(
				"""
					SELECT COALESCE(SUM(total_days), 0) FROM `tabWork From Home`
					WHERE employee = %s AND from_date BETWEEN %s AND %s
					AND docstatus = 1
				""",
				(self.employee, first_day_of_month, last_day_of_month),
			)
			total_wfh_days = total_wfh_days[0][0] if total_wfh_days else 0
		return True if total_wfh_days + self.total_days > 5 else False

	def before_submit(self):
		if self.status != "Approved" and self.status != "Rejected":
			frappe.throw(_("Only document with status 'Approved' or 'Rejected' can be submitable."))

	def after_insert(self):
		try:
			if self.status == "Requested":
				parent_doc = frappe.get_doc("Work From Home", self.name)
				args = parent_doc.as_dict()
				email_template = frappe.get_doc("Email Template", "WFH Request Template")
				message_res = frappe.render_template(email_template.response, args)
				sender_email = frappe.get_doc("User", frappe.session.user).email
				try:
					frappe.sendmail(
						recipients=self.approver_id,
						cc=self.team_lead_id,
						sender=sender_email,
						subject=email_template.subject,
						message=message_res,
						reference_doctype=self.doctype,
						reference_name=self.name,
					)
					frappe.msgprint(
						"Email sent to HR <b>"
						+ self.approver_id
						+ "</b> and the lead <b>"
						+ self.team_lead_id
						+ "</b>"
					)
				except frappe.OutgoingEmailError:
					pass
		except Exception as e:
			frappe.msgprint("Something went wrong! \n %s" % (str(e)))


@frappe.whitelist()
def send_wfh_feedback_forms():
	frappe.utils.logger.set_log_level("DEBUG")
	logger = frappe.logger("wfh_feedback", allow_site=True, file_count=10)
	sender = get_sender_email()
	yesterday = frappe.utils.add_days(frappe.utils.today(), -1)
	employees_wfh = frappe.db.sql(
		"""
			SELECT DISTINCT
				e.name AS employee_id,
				e.employee_name,
				e.team_lead,
				e.team_lead_name
			FROM `tabEmployee` e
			JOIN `tabWork From Home` wfh ON wfh.employee = e.name
			WHERE wfh.status = 'Approved'
			AND %(yesterday)s BETWEEN wfh.from_date AND wfh.to_date
		""",
		{"yesterday": yesterday},
		as_dict=True,
	)

	if not employees_wfh:
		return
	for employee in employees_wfh:
		email_template = frappe.get_doc("Email Template", "WFH Feedback Email")
		message_res = frappe.render_template(email_template.response, employee)
		frappe.sendmail(
			sender=sender, recipients=employee.team_lead, subject=email_template.subject, message=message_res
		)
		logger.info(
			f"Email has been sent to Team Lead: {employee.team_lead_name} for Employee: {employee.employee_name}"
		)


def get_sender_email() -> str | None:
	return frappe.db.get_single_value("HR Settings", "sender_email")


