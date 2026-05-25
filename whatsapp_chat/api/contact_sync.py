"""Sync Frappe Contact -> WhatsApp Contact.

Triggered from hooks.py on Contact.on_update so that when an operator fills in
the customer's name (or updates phone numbers) on a Frappe Contact, every
matching WhatsApp Contact picks up the same display name.

Matching is fuzzy: we normalize all phone variants the Contact owns
(`mobile_no` scalar + `phone_nos` child rows) and look up WhatsApp Contact
rows whose `mobile_no` matches any of them.
"""

import frappe


def _normalize_mx_phone(p):
    if not p:
        return None
    d = "".join(c for c in str(p) if c.isdigit())
    if d.startswith("521") and len(d) == 13:
        d = "52" + d[3:]
    elif len(d) == 10:
        d = "52" + d
    return d or None


def _full_name(contact):
    parts = [contact.first_name or "", contact.last_name or ""]
    nm = " ".join(p for p in parts if p).strip()
    return nm or contact.full_name or contact.name


def _collect_numbers(contact):
    """All phone variants attached to this Contact, normalized."""
    seen = set()
    for raw in [contact.mobile_no, contact.phone] + [p.phone for p in (contact.phone_nos or [])]:
        n = _normalize_mx_phone(raw)
        if n:
            seen.add(n)
    return seen


def on_contact_update(doc, method=None):
    """Push the Contact's display name to every matching WhatsApp Contact."""
    nums = _collect_numbers(doc)
    if not nums:
        return
    new_name = _full_name(doc)
    if not new_name:
        return
    matches = frappe.db.sql(
        "SELECT name, contact_name FROM `tabWhatsApp Contact` WHERE mobile_no IN %(nums)s",
        {"nums": tuple(nums)},
        as_dict=True,
    )
    for m in matches:
        if m["contact_name"] != new_name:
            frappe.db.set_value(
                "WhatsApp Contact", m["name"], "contact_name", new_name, update_modified=False
            )
