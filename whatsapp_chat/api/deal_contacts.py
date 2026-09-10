"""Return the WhatsApp-relevant contact list for a CRM Deal / CRM Lead.

Used by the frontend Activities WhatsApp tab to render one chat tab per Contact
attached to the deal, each with its own mobile_no, full_name and avatar.
"""

import frappe

from whatsapp_chat.api.native_workspace import require_legacy_customer_access


def _normalize_mx_phone(p):
    if not p:
        return None
    d = "".join(c for c in str(p) if c.isdigit())
    if d.startswith("521") and len(d) == 13:
        d = "52" + d[3:]
    elif len(d) == 10:
        d = "52" + d
    return d or None


def _number_has_whatsapp(norm):
    """Has this number ever been on WhatsApp with us — i.e. is there a WhatsApp
    Profile (created on every in/out message) or, failing that, any WhatsApp Message
    for it. Matched by trailing 10 digits so the Meta '1' prefix variants
    (5216691530561 inbound vs 526691530561 outbound) all group together. Used to flag
    numbers we've never exchanged WhatsApp with in the inbox."""
    digits = "".join(c for c in (norm or "") if c.isdigit())
    if len(digits) < 10:
        return False
    suf = "%" + digits[-10:]
    if frappe.db.exists("DocType", "WhatsApp Profiles"):
        if frappe.db.sql(
            "SELECT 1 FROM `tabWhatsApp Profiles` "
            "WHERE REGEXP_REPLACE(COALESCE(number,''),'[^0-9]','') LIKE %s LIMIT 1",
            (suf,),
        ):
            return True
    if frappe.db.exists("DocType", "WhatsApp Message"):
        if frappe.db.sql(
            "SELECT 1 FROM `tabWhatsApp Message` "
            "WHERE REGEXP_REPLACE(COALESCE(`from`,''),'[^0-9]','') LIKE %s "
            "   OR REGEXP_REPLACE(COALESCE(`to`,''),'[^0-9]','') LIKE %s LIMIT 1",
            (suf, suf),
        ):
            return True
    return False


def _last_incoming_ts(norm):
    """Newest Incoming WhatsApp Message creation for this number (trailing-10
    match, same grouping rule as _number_has_whatsapp), or None. Drives the
    'where is the live 24h session' default tab in the chat UI."""
    digits = "".join(c for c in (norm or "") if c.isdigit())
    if len(digits) < 10 or not frappe.db.exists("DocType", "WhatsApp Message"):
        return None
    suf = "%" + digits[-10:]
    row = frappe.db.sql(
        "SELECT MAX(creation) FROM `tabWhatsApp Message` "
        "WHERE type = 'Incoming' "
        "AND REGEXP_REPLACE(COALESCE(`from`,''),'[^0-9]','') LIKE %s",
        (suf,),
    )
    return row[0][0] if row and row[0] and row[0][0] else None


def _session_fields(norm):
    """last_incoming + session_open (inbound within Meta's 24h customer-service
    window → free-form sends deliver; outside it only templates do)."""
    from frappe.utils import add_to_date, get_datetime, now_datetime

    last_in = _last_incoming_ts(norm)
    return {
        "last_incoming": str(last_in) if last_in else None,
        "session_open": bool(
            last_in and get_datetime(last_in) > add_to_date(now_datetime(), hours=-24)
        ),
    }


def _manual_whatsapp_flag(doctype, name):
    """Operator-set `mobile_is_whatsapp` on the Deal/Lead (1=yes, 0=no), or None when
    the field isn't present yet (pre-migration). The field defaults to 1, so an
    explicit 0 means the operator marked the number as NOT on WhatsApp."""
    try:
        if not frappe.get_meta(doctype).has_field("mobile_is_whatsapp"):
            return None
    except Exception:
        return None
    v = frappe.db.get_value(doctype, name, "mobile_is_whatsapp")
    return None if v is None else int(v)


def _wa_state(manual, has):
    """Combine the operator flag with the conversation-derived signal.
    'yes' (on WhatsApp) wins when either is positive — a real exchange (has) trumps
    a mistaken 'no'. 'no' only when the operator explicitly unchecked it and there's
    no exchange. 'unknown' only before migration (flag absent) and never messaged."""
    if has or manual == 1:
        return "yes"
    if manual == 0:
        return "no"
    return "unknown"


@frappe.whitelist()
def get_deal_whatsapp_contacts(doctype: str, name: str):
    """For a CRM Deal: enumerate every Contact in the contacts child table with
    a usable mobile_no + the Contact's display name + image.
    For a CRM Lead: return a single virtual contact built from the Lead's own
    mobile_no + lead_name."""
    require_legacy_customer_access()
    if doctype not in ("CRM Deal", "CRM Lead"):
        frappe.throw("Unsupported doctype")
    if not frappe.has_permission(doctype, "read", doc=name):
        frappe.throw("Forbidden", frappe.PermissionError)

    if doctype == "CRM Lead":
        d = frappe.db.get_value(
            "CRM Lead", name, ["lead_name", "mobile_no", "image"], as_dict=True
        )
        if not d:
            return []
        phone_norm = _normalize_mx_phone(d.mobile_no)
        if not phone_norm:
            return []
        manual = _manual_whatsapp_flag("CRM Lead", name)
        has = _number_has_whatsapp(phone_norm)
        return [
            {
                "contact": None,
                "name": d.lead_name or d.mobile_no,
                "phone": phone_norm,
                "phone_display": d.mobile_no,
                "image": d.image,
                "is_primary": 1,
                "has_whatsapp": has,
                "whatsapp_state": _wa_state(manual, has),
                **_session_fields(phone_norm),
            }
        ]

    # CRM Deal: walk the CRM Contacts child table
    rows = frappe.db.sql(
        """SELECT cc.contact AS contact, cc.is_primary, c.full_name, c.mobile_no, c.image
           FROM `tabCRM Contacts` cc
           LEFT JOIN `tabContact` c ON c.name = cc.contact
           WHERE cc.parent = %s AND cc.parenttype = 'CRM Deal'
           ORDER BY cc.is_primary DESC, cc.idx ASC""",
        (name,),
        as_dict=True,
    )
    out = []
    seen_phones = set()
    manual = _manual_whatsapp_flag("CRM Deal", name)
    for r in rows:
        # EVERY number of the Contact becomes its own conversation tab, not just
        # the primary. A customer often writes from a second number (spouse's
        # phone, work line): the live session then exists on THAT number, and a
        # send to the primary bounces with Meta 131047 (outside the 24h window).
        # Order: primary mobile first, then child-table order.
        phones = []
        if r.get("mobile_no"):
            phones.append(r["mobile_no"])
        if r.get("contact"):
            for p in frappe.get_all(
                "Contact Phone",
                filters={"parent": r["contact"], "parenttype": "Contact"},
                fields=["phone"],
                order_by="is_primary_mobile_no desc, idx asc",
            ):
                if p.phone:
                    phones.append(p.phone)
        first_for_contact = True
        for phone in phones:
            norm = _normalize_mx_phone(phone)
            if not norm or norm in seen_phones:
                continue
            seen_phones.add(norm)
            has = _number_has_whatsapp(norm)
            out.append(
                {
                    "contact": r.get("contact"),
                    "name": r.get("full_name") or r.get("contact") or norm,
                    "phone": norm,
                    "phone_display": phone,
                    "image": r.get("image"),
                    "is_primary": int(r.get("is_primary") or 0) if first_for_contact else 0,
                    "has_whatsapp": has,
                    "whatsapp_state": _wa_state(manual, has),
                    **_session_fields(norm),
                }
            )
            first_for_contact = False

    # Fall-through: if no contacts, expose the Deal's own mobile_no as a single tab
    if not out:
        deal_mobile = frappe.db.get_value("CRM Deal", name, "mobile_no")
        norm = _normalize_mx_phone(deal_mobile)
        if norm:
            has = _number_has_whatsapp(norm)
            out.append(
                {
                    "contact": None,
                    "name": deal_mobile,
                    "phone": norm,
                    "phone_display": deal_mobile,
                    "image": None,
                    "is_primary": 1,
                    "has_whatsapp": has,
                    "whatsapp_state": _wa_state(manual, has),
                    **_session_fields(norm),
                }
            )
    return out
