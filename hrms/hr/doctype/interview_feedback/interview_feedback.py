# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Avg
from frappe.utils import flt, get_link_to_form, getdate


class InterviewFeedback(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from hrms.hr.doctype.skill_assessment.skill_assessment import SkillAssessment

		amended_from: DF.Link | None
		average_rating: DF.Float
		feedback: DF.Text
		interview: DF.Link
		interview_type: DF.Link
		interviewer: DF.Link
		job_applicant: DF.Link | None
		result: DF.Literal["", "Cleared", "Rejected"]
		skill_assessment: DF.Table[SkillAssessment]
	# end: auto-generated types

	def validate(self):
		self.validate_interviewer()
		self.validate_interview_date()
		self.validate_duplicate()
		self.calculate_average_rating()

	def on_submit(self):
		self.update_interview_average_rating()

	def on_cancel(self):
		self.update_interview_average_rating()

	def validate_interviewer(self):
		applicable_interviewers = get_applicable_interviewers(self.interview)
		if self.interviewer not in applicable_interviewers:
			frappe.throw(
				_("{0} is not allowed to submit Interview Feedback for the Interview: {1}").format(
					frappe.bold(self.interviewer), frappe.bold(self.interview)
				)
			)

	def validate_interview_date(self):
		scheduled_date = frappe.db.get_value("Interview", self.interview, "scheduled_on")

		if getdate() < getdate(scheduled_date) and self.docstatus == 1:
			frappe.throw(
				_("Submission of {0} before {1} is not allowed").format(
					frappe.bold(_("Interview Feedback")), frappe.bold(_("Interview Scheduled Date"))
				)
			)

	def validate_duplicate(self):
		duplicate_feedback = frappe.db.exists(
			"Interview Feedback",
			{"interviewer": self.interviewer, "interview": self.interview, "docstatus": 1},
		)

		if duplicate_feedback:
			frappe.throw(
				_(
					"Feedback already submitted for the Interview {0}. Please cancel the previous Interview Feedback {1} to continue."
				).format(self.interview, get_link_to_form("Interview Feedback", duplicate_feedback))
			)

	def calculate_average_rating(self):
		# Bitsol: ratings are half-point Selects ("0", "0.5" ... "5"); unrated skills are ignored
		total_rating = 0
		valid_ratings_count = 0
		for d in self.skill_assessment:
			rating = parse_rating(d.rating)
			if rating is not None:
				total_rating += rating
				valid_ratings_count += 1

		self.average_rating = flt(total_rating / valid_ratings_count if valid_ratings_count else 0)

	def update_interview_average_rating(self):
		interview_feedback = frappe.qb.DocType("Interview Feedback")
		query = (
			frappe.qb.from_(interview_feedback)
			.where(
				(interview_feedback.interview == self.interview)
				& (interview_feedback.docstatus == 1)
				& (interview_feedback.average_rating.isnotnull())
			)
			.select(Avg(interview_feedback.average_rating).as_("average"))
		)
		data = query.run(as_dict=True)
		average_rating = data[0]["average"] if data and data[0] and data[0]["average"] is not None else 0.0

		interview = frappe.get_doc("Interview", self.interview)
		interview.db_set("average_rating", average_rating)
		interview.notify_update()


def parse_rating(rating) -> float | None:
	"""Return the rating as a float, or None when the skill was not rated."""
	if rating is None or (isinstance(rating, str) and not rating.strip()):
		return None
	try:
		return float(rating)
	except (TypeError, ValueError):
		return None


@frappe.whitelist()
def get_applicable_interviewers(interview: str) -> list[str]:
	frappe.has_permission("Interview", "read", interview, throw=True)
	return frappe.get_all("Interview Detail", filters={"parent": interview}, pluck="interviewer")
