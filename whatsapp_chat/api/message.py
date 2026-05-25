import frappe
import mimetypes



@frappe.whitelist()
def get_all(room: str, user_no: str):
    """Get all the messages of a particular room

    Args:
        room (str): Room's name.

    """
    return frappe.db.sql("""
        SELECT creation,
        case
            when `to` <> '' then `to`
            else
            'Administrator'
        end as sender_user_no,
        case
            when COALESCE(content_type, 'text') = 'text' then COALESCE(message, '')
            else COALESCE(attach, message, '')
        end as content,
        case
            when COALESCE(content_type, 'text') <> 'text' then message
            else NULL
        end as caption,
        COALESCE(content_type, 'text') as content_type
        from `tabWhatsApp Message` where (`to` = %(user_no)s or `from` = %(user_no)s)
        AND COALESCE(message_type, '') <> 'Template'
        order by creation asc
    """, {"user_no": user_no}, as_dict=True)


@frappe.whitelist()
def mark_as_read(room):
    """Mark messages as read in local DB and optionally send read receipts to WhatsApp."""
    try:
        # Update local contact status
        frappe.db.set_value("WhatsApp Contact", room, "is_read", 1, update_modified=False)

        # Send read receipts to WhatsApp if enabled
        send_whatsapp_read_receipts(room)
    except Exception:
        pass  # Ignore concurrent update errors
    return "ok"


def send_whatsapp_read_receipts(room):
    """Send read receipts to WhatsApp for unread incoming messages."""
    try:
        # Get the contact's mobile number
        contact = frappe.get_doc("WhatsApp Contact", room)
        if not contact.mobile_no:
            return

        # Find unread incoming messages for this contact
        unread_messages = frappe.get_all(
            "WhatsApp Message",
            filters={
                "from": contact.mobile_no,
                "type": "Incoming",
                "status": ["not in", ["marked as read"]]
            },
            fields=["name", "whatsapp_account"],
            order_by="creation desc",
            limit=10
        )

        if not unread_messages:
            return

        # Check if auto read receipt is enabled for the account
        for msg in unread_messages:
            if not msg.whatsapp_account:
                continue

            allow_auto_read = frappe.db.get_value(
                "WhatsApp Account",
                msg.whatsapp_account,
                "allow_auto_read_receipt"
            )

            if allow_auto_read:
                try:
                    msg_doc = frappe.get_doc("WhatsApp Message", msg.name)
                    msg_doc.send_read_receipt()
                except Exception as e:
                    frappe.log_error(f"Failed to send read receipt for {msg.name}: {str(e)}", "WhatsApp Chat Read Receipt")
    except Exception as e:
        frappe.log_error(f"send_whatsapp_read_receipts error: {str(e)}", "WhatsApp Chat Read Receipt")



@frappe.whitelist()
def send(content, user, room, user_no, attachment=None):
    content_type = "text"
    if attachment:
        file_type = mimetypes.guess_type(content)[0]
        if file_type in ["image/apng","image/avif","image/gif","image/jpeg","image/png","image/svg","image/webp"]:
            content_type = 'image'
        elif file_type in ["application/pdf", "application/vnd.ms-powerpoint", "application/msword", "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]:
            content_type = "document"
        elif file_type in ["audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg"]:
            content_type = 'audio'
        elif file_type in ["video/mp4", "video/3gp"]:
            content_type = "video"

        frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": user_no,
            "type": "Outgoing",
            "attach": content,
            "content_type": content_type
        }).save()
    else:
        frappe.get_doc({
            "doctype": "WhatsApp Message",
            "to": user_no,
            "type": "Outgoing",
            "message": content,
            "content_type": content_type
        }).save()

    return "ok"


def _normalize_mx_phone(p):
    """Coerce phone numbers to digits-only canonical MX form so chat history groups by one identity.
    Examples: +526691530561, 6691530561, 5216691530561  -> 526691530561"""
    if not p:
        return p
    d = "".join(c for c in str(p) if c.isdigit())
    if d.startswith("521") and len(d) == 13:
        d = "52" + d[3:]
    elif len(d) == 10:
        d = "52" + d
    return d


def _resolve_contact_name(mobile_no, fallback):
    """Look up a human-readable name for a phone number via CRM Lead/Deal/Contact."""
    try:
        from crm.integrations.api import get_contact_by_phone_number
        c = get_contact_by_phone_number(mobile_no) or {}
        if c.get("full_name"):
            return c["full_name"]
        if c.get("name"):
            full = frappe.db.get_value("Contact", c["name"], ["first_name", "last_name"])
            if full and any(full):
                return " ".join(x for x in full if x).strip()
    except Exception:
        pass
    return fallback or mobile_no


def _default_assignee_email():
    """Pick the user new WhatsApp Contacts default to so realtime events fire."""
    val = frappe.db.get_single_value("WhatsApp Settings", "default_assignee_email") if frappe.db.has_column("WhatsApp Settings", "default_assignee_email") else None
    if val:
        return val
    return frappe.db.get_value("Has Role", {"role": "System Manager", "parenttype": "User"}, "parent", order_by="creation asc")


def last_message(doc, method):
    if doc.type == 'Outgoing':
        mobile_no = doc.to
    else:
        mobile_no = doc.get("from")

    mobile_no = _normalize_mx_phone(mobile_no)

    contact_name = frappe.db.get_value("WhatsApp Contact", filters={"mobile_no": mobile_no})
    if contact_name:
        chat_doc = frappe.get_doc("WhatsApp Contact", contact_name)
        chat_doc.last_message = doc.message
        chat_doc.is_read = 0
        chat_doc.save(ignore_permissions=True)
    else:
        chat_doc = frappe.get_doc({
            "doctype": "WhatsApp Contact",
            "mobile_no": mobile_no,
            "last_message": doc.message,
            "contact_name": _resolve_contact_name(mobile_no, doc.get("profile_name") or mobile_no),
            "is_read": 0,
            "email": _default_assignee_email(),
        })
        chat_doc.save(ignore_permissions=True)

    if chat_doc.email and doc.type != 'Outgoing':
        message_data = {
            "content": doc.message or doc.attach or '',
            "creation": frappe.utils.now(),
            "room": chat_doc.name,
            "contact_name": chat_doc.contact_name,
            "sender_user_no": mobile_no,
            "user": "Guest"
        }
        # Notify chat list
        frappe.publish_realtime(
            "latest_chat_updates",
            message_data,
            user=chat_doc.email
        )
        # Notify open chat room
        frappe.publish_realtime(
            chat_doc.name,
            message_data,
            user=chat_doc.email
        )

    return "ok"
