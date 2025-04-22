{% set emails = ["mishael.mushtaq@bitsol.tech", "fiza.mahmood@bitsol.tech"] %}
{% set slack_mentions = "" %}
{% for email in emails %}
  {% set slack_id = frappe.db.get_value("Employee", {"user_id": email}, "custom_slack_user_id") %}
  {% if slack_id %}
    {% set slack_mentions = slack_mentions + "<@" + slack_id + ">, " %}
  {% endif %}
{% endfor %}
{% set slack_mentions = slack_mentions.rstrip(", ") %}

{% set team_lead_slack_id = frappe.db.get_value("Employee", {"user_id": doc.team_lead_id}, "custom_slack_user_id") %}
{% set cc_line = team_lead_slack_id and ("\nCC: <@" ~ team_lead_slack_id ~ ">") or "" %}

{{ slack_mentions }} *{{ doc.employee_name }}* has applied for *Work From Home* from *{{ doc.from_date }}* to *{{ doc.to_date }}*.{{ cc_line }}
