import frappe

def execute(filters=None):
    columns = [
        {"label": "Employee", "fieldname": "employee", "fieldtype": "Link", "options": "Employee"},
        {"label": "Employee Name", "fieldname": "employee_name", "fieldtype": "Data"},
        {"label": "Reimbursement Type", "fieldname": "reimbursement_type", "fieldtype": "Link", "options": "Reimbursement Types"},
        {"label": "Availed Amount", "fieldname": "total_amount", "fieldtype": "Currency"},
        {"label": "Medical Allowance", "fieldname": "medical_allowance", "fieldtype": "Currency"},
        {"label": "Medical Balance", "fieldname": "medical_balance", "fieldtype": "Currency"},
    ]

    conditions = "r.status = 'Approved' AND r.docstatus = 1"
    params = []

    if filters.get("from_date"):
        conditions += " AND r.creation >= %s"
        params.append(filters["from_date"])

    if filters.get("to_date"):
        conditions += " AND r.creation <= %s"
        params.append(filters["to_date"])

    query = f"""
        SELECT 
            r.employee, 
            r.employee_name, 
            r.reimbursement_type, 
            SUM(r.total_amount) AS total_amount,
            CASE 
                WHEN r.reimbursement_type = 'Medical' THEN e.medical_allowance 
                ELSE NULL 
            END AS medical_allowance,
            CASE 
                WHEN r.reimbursement_type = 'Medical' THEN e.medical_balance 
                ELSE NULL 
            END AS medical_balance
        FROM `tabReimbursement` r
        LEFT JOIN `tabEmployee` e ON e.name = r.employee
        WHERE {conditions}
        GROUP BY r.employee, r.reimbursement_type
        ORDER BY r.employee
    """

    data = frappe.db.sql(query, tuple(params), as_dict=True)
    return columns, data
