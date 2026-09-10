"""Finite retirement gate for the legacy, phone-only customer widget.

An absent optional CRM app or a truly old CRM without any native footprint
preserves the old behavior. A partial/broken native install must never reopen
the legacy customer APIs. This probe does not grant access to a thread.
"""

from importlib import import_module

import frappe


CUSTOMER_WORKSPACE = "/desk/customer-conversations"
USE_NATIVE = "Use native Conversations."
_APIS = {
    "crm.api.conversation_threads": ("list_accounts", "list_threads", "open_thread", "get_history"),
    "crm.api.conversations": ("get_or_create", "apply_control"),
    "crm.api.outbox": ("queue_message",),
}
_SCHEMAS = {
    "CRM Conversation": {
        "provider", "account_id", "peer_id", "generation", "control_state", "bot_enabled",
    },
    "CRM Conversation Control Event": {
        "conversation", "command_key", "from_generation", "to_generation", "result_json",
    },
    "CRM Outbound Intent": {
        "conversation", "conversation_generation", "provider", "account_id", "peer_id",
        "action_key", "payload_hash", "payload", "state",
    },
}


def workspace_state():
    """Return ``legacy``, ``native`` or ``unavailable`` from this site's server.

    No global cache: errors or a stale client capability cannot restore a retired
    customer route. Only fixed metadata/API names are inspected, never messages.
    """
    try:
        if "crm" not in frappe.get_installed_apps():
            return "legacy"
        page_exists = bool(frappe.db.exists("Page", "customer-conversations"))
        schemas_exist = {doctype: bool(frappe.db.exists("DocType", doctype)) for doctype in _SCHEMAS}
        tables_exist = {doctype: bool(frappe.db.table_exists(doctype)) for doctype in _SCHEMAS}
        modules = {}
        for path in _APIS:
            try:
                modules[path] = import_module(path)
            except ModuleNotFoundError as exc:
                # Only absence of this exact service means an old CRM. A missing
                # dependency within an installed service is a broken deployment.
                if exc.name != path:
                    return "unavailable"
        if not (page_exists or any(schemas_exist.values()) or any(tables_exist.values()) or modules):
            return "legacy"
        if not page_exists or not all(schemas_exist.values()) or len(modules) != len(_APIS):
            return "unavailable"
        for doctype, fields in _SCHEMAS.items():
            if not tables_exist[doctype]:
                return "unavailable"
            if not fields.issubset(set(frappe.db.get_table_columns(doctype))):
                return "unavailable"
        if not all(callable(getattr(modules[path], name, None)) for path, names in _APIS.items() for name in names):
            return "unavailable"
    except Exception:
        # A broken installed app is not permission to expose phone-only history.
        # Do not log exception text: readiness is a static, non-customer result.
        return "unavailable"
    return "native"


def require_legacy_customer_access():
    """Deny legacy customer reads/sends before any customer lookup or side effect."""
    if workspace_state() != "legacy":
        raise frappe.PermissionError(USE_NATIVE)


def legacy_customer_hooks_enabled():
    """Retired notification/identity hooks must no-op, not abort core ingestion."""
    return workspace_state() == "legacy"
