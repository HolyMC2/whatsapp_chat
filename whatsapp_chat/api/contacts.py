import frappe

from whatsapp_chat.api.native_workspace import require_legacy_customer_access



@frappe.whitelist()
def create(contact_name, mobile_no, email):
    """Create contact."""
    require_legacy_customer_access()
    frappe.get_doc({
        "doctype": "WhatsApp Contact",
        "contact_name": contact_name,
        "mobile_no": mobile_no,
        "email": email
    }).save()
    return "email"


@frappe.whitelist()
def get(email):
    """Get all contacts assigned to email."""
    require_legacy_customer_access()
    return frappe.db.get_all(
        "WhatsApp Contact",
        filters={"email": ['in', [email, '']]},
        fields=["*"])
    return data
