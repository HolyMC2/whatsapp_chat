import frappe

from whatsapp_chat.api.native_workspace import CUSTOMER_WORKSPACE, workspace_state


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
