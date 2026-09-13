import json
import logging
from importlib import import_module

import frappe

from whatsapp_chat.api.native_workspace import CUSTOMER_WORKSPACE, workspace_state

# The legacy guest token is minted OUTSIDE this app: the shipped widget calls
# `chat.api.user.get_guest_room` (public/js/components/chat_utils.js), an
# endpoint of the upstream `chat` app. whatsapp_chat itself has no token store -
# one DocType (WhatsApp Contact), no Chat Room, Chat Profile or Chat Token - so
# only that app can say whether a token grants a room. These are the two places
# it has kept its verifier; a callable at either is used, anything else is not
# verified.
_LEGACY_TOKEN_VALIDATORS = (('chat.utils', 'validate_token'), ('chat.api.user', 'validate_token'))

_NO_TOKEN_VALIDATOR_LOGGED = False


@frappe.whitelist(allow_guest=True)
def settings(token):
    """Fetch and return the settings for a chat session

    Args:
        token (str): Guest token.

    """
    config = {
        'socketio_port': frappe.conf.socketio_port,
        'user_email': frappe.session.user,
        'is_admin': True if 'user_type' in frappe.session.data else False,
        'guest_title': ''.join(frappe.get_hooks('guest_title')),
    }

    config = {**config, **get_chat_settings()}

    # The bundle checks enable_chat before creating either the FAB or navbar.
    # No guest-token processing or legacy customer/user lookups on this path.
    if not config['enable_chat']:
        return config

    if config['is_admin']:
        config['user'] = get_admin_name(config['user_email'])
        config['user_settings'] = get_user_settings()
    else:
        config['user'] = 'Guest'
        token_verify = validate_token(token)
        if token_verify[0] is True:
            config['room'] = token_verify[1]['room']
            config['user_email'] = token_verify[1]['email']
            config['is_verified'] = True
        else:
            config['is_verified'] = False

    return config


def validate_token(token):
    """Resolve a legacy guest token to its room. Never raises, never leaks.

    `settings()` has called this name since the app's first commit and it was
    never defined anywhere in the repo, so the guest branch below raised
    NameError - a guaranteed 500 on an `allow_guest=True` endpoint. That branch
    is reached only when `workspace_state()` is "legacy", i.e. when the crm app
    is ABSENT (or present with zero native footprint): with crm installed,
    `get_chat_settings()` answers `enable_chat: False` and `settings()` returns
    before this call. A 403 keyed on "crm is not installed" would therefore fire
    on exactly the path the legacy branch exists to serve, and crm has no token
    validator to import in the other case (checked: no `validate_token` in that
    app at all; its only verify-token is an unrelated Exotel voice webhook key).

    Contract kept for the caller: `(verified, {"room": ..., "email": ...})`.
    An unverifiable token answers `(False, None)`, which the widget renders as
    its welcome screen - the same answer the everyday first visit needs, since
    the bundle sends an EMPTY token until a guest room exists
    (`localStorage.getItem('guest_token') || ''`). Refusing with a permission
    error here would break every first-time visitor, so the guard degrades
    instead of refusing, and the response body carries no diagnostic detail.
    """
    if not token:
        return False, None

    validator = _legacy_token_validator()
    if validator is None:
        _warn_missing_token_validator()
        return False, None

    try:
        verified, payload = validator(token)
    except Exception:
        # A verifier that raises is a verifier that did not verify. Never
        # surface its text: this response reaches an unauthenticated caller.
        return False, None

    if verified is not True or not isinstance(payload, dict):
        return False, None
    if not payload.get('room') or not payload.get('email'):
        return False, None
    return True, {'room': payload['room'], 'email': payload['email']}


def _legacy_token_validator():
    """The upstream `chat` app's guest-token verifier, or None if unavailable."""
    try:
        if 'chat' not in frappe.get_installed_apps():
            return None
    except Exception:
        return None

    for module_name, attribute in _LEGACY_TOKEN_VALIDATORS:
        try:
            module = import_module(module_name)
        except Exception:
            continue
        validator = getattr(module, attribute, None)
        if callable(validator):
            return validator
    return None


def _warn_missing_token_validator():
    """One rotating-file breadcrumb per process, never a `tabError Log` row.

    A guest presenting a non-empty token on a site that cannot verify one is an
    operator-visible misconfiguration (the legacy widget also cannot MINT a
    token there - `chat.api.user.get_guest_room` belongs to the same absent
    app). Silence is what hid the NameError for months; an unbounded row
    producer on a guest endpoint would be the opposite mistake, so this is a
    file logger, once per process, with no token material in it.

    The logger is resolved PER CALL and its level raised, both deliberately.
    Frappe caches one logger per `<module>-<site>` pair and creates it at
    `logging.ERROR` when `DEV_SERVER` is unset and `log_level` is absent from
    common_site_config.json - which is how this bench and cell-0 run. Measured
    here on 2026-09-12: the first version of this breadcrumb wrote 0 bytes to
    both `logs/whatsapp_chat.log` and the per-site copy. Capturing the logger at
    import would additionally pin every line to whichever tenant imported the
    module first on a shared bench.
    """
    global _NO_TOKEN_VALIDATOR_LOGGED
    if _NO_TOKEN_VALIDATOR_LOGGED:
        return
    _NO_TOKEN_VALIDATOR_LOGGED = True
    try:
        logger = frappe.logger('whatsapp_chat')
        level = getattr(logger, 'level', None)
        set_level = getattr(logger, 'setLevel', None)
        if isinstance(level, int) and level > logging.INFO and callable(set_level):
            set_level(logging.INFO)
        logger.warning(
            json.dumps(
                {
                    'app': 'whatsapp_chat',
                    'scope': 'guest_token.no_verifier',
                    'site': str(getattr(frappe.local, 'site', '') or 'unknown-site'),
                    'msg': 'legacy guest token presented but no verifier is installed; '
                    'answering not verified (the `chat` app mints and verifies these tokens)',
                },
                sort_keys=True,
            )
        )
    except Exception:
        # A breadcrumb is never worth failing the guest response it describes.
        pass


def get_admin_name(user_key):
    """Get the admin name for specified user key"""
    full_name = frappe.db.get_value('User', user_key, 'full_name')
    return full_name

def get_chat_settings():
    """Get the chat settings
    Returns:
        dict: Dictionary containing chat settings.
    """
    state = workspace_state()
    if state != "legacy":
        return {
            'enable_chat': False,
            'customer_workspace': CUSTOMER_WORKSPACE if state == "native" else None,
            'customer_workspace_status': state,
        }

    # chat_settings = frappe.get_cached_doc('Chat Settings')
    # user_roles = frappe.get_roles()

    # allowed_roles = [u.role for u in chat_settings.allowed_roles]
    # allowed_roles.extend(['System Manager', 'Administrator'])
    result = {
        'enable_chat': False
    }

    # if frappe.session.user == 'Guest':
    #     result['enable_chat'] = True

    # if not chat_settings.enable_chat or not has_common(allowed_roles, user_roles):
    #     return result

    # chat_settings.chat_operators = [co.user for co in chat_settings.chat_operators]

    # if chat_settings.start_time and chat_settings.end_time:
    #     start_time = datetime.time.fromisoformat(chat_settings.start_time)
    #     end_time = datetime.time.fromisoformat(chat_settings.end_time)
    #     current_time = datetime.datetime.now().time()

    #     chat_status = 'Online' if time_in_range(
    #         start_time, end_time, current_time) else 'Offline'
    # else:
    #     chat_status = 'Online'

    result['enable_chat'] = True
    result['chat_status'] = "Online"
    return result

def get_user_settings():
    """Get the user settings

    Returns:
        dict: user settings
    """
    # if frappe.db.exists('Chat User Settings', frappe.session.user):
    #     user_doc = frappe.db.get_value('Chat User Settings', frappe.session.user, [
    #         'enable_message_tone', 'enable_notifications'], as_dict=1)
    # else:
    user_doc = {
        'enable_message_tone': 1,
        'enable_notifications': 1
    }

    return user_doc
