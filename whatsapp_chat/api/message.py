import frappe
import mimetypes

from whatsapp_chat.api.native_workspace import legacy_customer_hooks_enabled, require_legacy_customer_access



@frappe.whitelist()
def get_all(room: str, user_no: str):
    """Get all the messages of a particular room

    Args:
        room (str): Room's name.

    """
    require_legacy_customer_access()
    return frappe.db.sql("""
        SELECT name, creation, type, status,
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
    require_legacy_customer_access()
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
    require_legacy_customer_access()
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
    require_legacy_customer_access()
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
    """Look up a human-readable name for a phone number via CRM helper, with a
    direct Contact / Contact Phone fallback for the cases CRM's strict matching
    misses (e.g. legacy MX `521…` mobile_no formats)."""
    # 1. CRM helper (fast path)
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

    # 2. Direct fallback: search Contact + Contact Phone with normalized phone.
    # Strip non-digits and try matching the last 10 digits — that's the user's
    # local MX number and survives all the +/521 prefix permutations.
    digits = "".join(c for c in str(mobile_no or "") if c.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else digits
    if not last10:
        return fallback or mobile_no
    try:
        # match against Contact.mobile_no normalised (strip + and spaces)
        rows = frappe.db.sql(
            """SELECT c.name, c.full_name, c.first_name, c.last_name
               FROM `tabContact` c
               WHERE REPLACE(REPLACE(c.mobile_no,'+',''),' ','') LIKE %s
                  OR EXISTS (
                     SELECT 1 FROM `tabContact Phone` p
                     WHERE p.parent = c.name
                       AND REPLACE(REPLACE(p.phone,'+',''),' ','') LIKE %s
                  )
               ORDER BY c.modified DESC LIMIT 1""",
            (f"%{last10}", f"%{last10}"),
            as_dict=True,
        )
        if rows:
            r = rows[0]
            if r.get("full_name") and r["full_name"].strip():
                return r["full_name"].strip()
            nm = " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x).strip()
            if nm:
                return nm
    except Exception:
        pass
    return fallback or mobile_no


def _default_assignee_email():
    """Pick the user new WhatsApp Contacts default to so realtime events fire."""
    # WhatsApp Settings is a Single doctype (no `tabWhatsApp Settings` table), so
    # has_column() raises TableMissingError on it — which previously blew up the
    # WhatsApp Message after_insert hook for every brand-new number (no existing
    # WhatsApp Contact), failing both inbound webhooks and outbound sends. Guard
    # with meta.has_field (Single-safe) instead; get_single_value reads tabSingles.
    val = None
    if (
        frappe.db.exists("DocType", "WhatsApp Settings")
        and frappe.get_meta("WhatsApp Settings").has_field("default_assignee_email")
    ):
        val = frappe.db.get_single_value("WhatsApp Settings", "default_assignee_email")
    if val:
        return val
    return frappe.db.get_value("Has Role", {"role": "System Manager", "parenttype": "User"}, "parent", order_by="creation asc")


def _looks_like_phone(s):
    """Heuristic: a contact_name that's still phone-shaped (no real name resolved yet)."""
    if not s:
        return True
    s = str(s).strip().lstrip("+")
    return s.isdigit() and len(s) >= 7


def _resolve_deal_or_lead(mobile_no):
    """Return (reference_doctype, reference_name) for the latest-modified Deal
    or Lead whose mobile_no matches the last 10 digits of `mobile_no`.

    Prefer Deal over Lead; pick most recently modified within either pool.
    Returns (None, None) if nothing matches.
    """
    digits = "".join(c for c in str(mobile_no or "") if c.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else digits
    if not last10:
        return None, None
    like = f"%{last10}"
    deal = frappe.db.sql(
        """SELECT name FROM `tabCRM Deal`
           WHERE REPLACE(REPLACE(mobile_no,'+',''),' ','') LIKE %s
           ORDER BY modified DESC LIMIT 1""",
        (like,),
    )
    if deal:
        return "CRM Deal", deal[0][0]
    lead = frappe.db.sql(
        """SELECT name FROM `tabCRM Lead`
           WHERE COALESCE(converted,0)=0
             AND REPLACE(REPLACE(mobile_no,'+',''),' ','') LIKE %s
           ORDER BY modified DESC LIMIT 1""",
        (like,),
    )
    if lead:
        return "CRM Lead", lead[0][0]
    return None, None


def auto_link_reference(doc, method=None):
    """before_insert hook: stamp reference_doctype/reference_name on a new
    WhatsApp Message so the CRM Deal/Lead WhatsApp tab scopes correctly.
    Skips if reference is already set (e.g. operator chose target explicitly)."""
    if not legacy_customer_hooks_enabled():
        return
    if doc.get("reference_doctype") and doc.get("reference_name"):
        return
    phone = doc.to if doc.type == "Outgoing" else doc.get("from")
    dt, name = _resolve_deal_or_lead(_normalize_mx_phone(phone))
    if dt:
        doc.reference_doctype = dt
        doc.reference_name = name


def on_message_status_change(doc, method=None):
    """Push a realtime ping when an outbound message's delivery status changes, so
    the open inbox thread re-renders its ✓/✓✓/read receipt live. Meta's status
    webhook sets WhatsApp Message.status (sent→delivered→read→failed) but emits no
    realtime, so without this the receipts only update on a full thread reload."""
    if not legacy_customer_hooks_enabled():
        return
    if not doc.has_value_changed("status"):
        return
    if not (doc.get("reference_doctype") and doc.get("reference_name")):
        return
    # after_commit: the receipt webhook runs in a transaction — publishing before
    # commit let the frontend refetch a not-yet-visible status (stale ✓/✓✓ race).
    frappe.publish_realtime(
        "whatsapp_status",
        {"reference_doctype": doc.reference_doctype, "reference_name": doc.reference_name},
        after_commit=True,
    )


def last_message(doc, method):
    if not legacy_customer_hooks_enabled():
        return
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
        # Heal name: if previous contact_name still looks like a phone, try resolving again
        # (covers contacts created before a matching Frappe Contact existed).
        if _looks_like_phone(chat_doc.contact_name):
            resolved = _resolve_contact_name(mobile_no, doc.get("profile_name") or mobile_no)
            if resolved and not _looks_like_phone(resolved):
                chat_doc.contact_name = resolved
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

    # Realtime for the fcrm inbox + the Deal/Lead Conversación tab. crm only publishes
    # `whatsapp_message` in on_update (status changes), so a NEW message (insert) pushed
    # nothing and the agent had to F5. Broadcast (no user/doctype -> site room, all agents)
    # + after_commit so the frontend's refetch reads the COMMITTED row (no stale race).
    # UNGATED on reference: an inbound from an unknown number (reference empty ->
    # "Sin asignar") previously emitted NOTHING, so first-contact messages only
    # appeared on F5 — Messenger fixed this (services/messenger.publish_thread_update
    # fires for orphans); WhatsApp kept the gate. The payload carries the phone so
    # the frontend can refresh an open orphan thread too.
    payload = {
        "reference_doctype": doc.get("reference_doctype"),
        "reference_name": doc.get("reference_name"),
        "phone": mobile_no,
        "direction": "out" if doc.type == "Outgoing" else "in",
    }
    frappe.publish_realtime("whatsapp_message", payload, after_commit=True)
    if doc.get("reference_doctype") == "CRM Deal":
        frappe.publish_realtime(
            "doco_marketing:thread_update",
            {"deal": doc.reference_name, "channel": "whatsapp"},
            after_commit=True,
        )

    return "ok"
