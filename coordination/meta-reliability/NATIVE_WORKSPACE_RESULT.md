# Legacy WhatsApp widget transition — 2026-09-10

Implemented in isolated branch `fix/meta-native-workspace-20260910`, base
`1a0d6f6c13a414a7d6ab0acc1ba1b08376714f8e`. Shared checkout, Doco private assistant,
CRM, installed apps, schemas, global settings and production remain unchanged.
No commit, staging, migration, restart or rollout performed.

## Behavior

`whatsapp_chat.api.native_workspace.workspace_state()` inspects only fixed server
metadata and API exports. Missing CRM retains the historical behavior. An installed
older CRM retains it only when there is no native Page, DocType, physical table or
importable native service. Any partial native footprint blocks legacy access. An
imported service's missing dependency, other import/probe error, missing table,
column or export produces static `unavailable`, never a legacy fallback.

Ready requires Page `customer-conversations`, the three Conversation/Control Event/
Outbound Intent physical schemas and the fixed broker/control/queue exports. Ready
settings return `enable_chat: false`, `customer_workspace_status: "native"`, and
`customer_workspace: "/desk/customer-conversations"`. Partial/broken native settings
also disable the widget, with status `unavailable` and a null route. This is routing
metadata, not thread access authorization; the native workspace owns current scope.

The existing unmodified bundle returns before `create_app()` when `enable_chat`
is false. That suppresses both the FAB and navbar icon and avoids socket setup.
No second launcher or new global hide rule was introduced. The ordinary native
Desk Page remains the customer entry point, alongside the unchanged Doco assistant.

## Customer-only API and hook coverage

| File | Protected branch |
|---|---|
| `api/message.py` | `get_all` phone-only transcript; `send` text/attachment; `mark_as_read`; internal `send_whatsapp_read_receipts` |
| `api/contacts.py` | `get(email)` contact/last-message projection; `create` |
| `api/deal_contacts.py` | Both Lead and Deal branches of `get_deal_whatsapp_contacts`, including trailing-phone presence/session lookups |

Each guard raises the static PermissionError `Use native Conversations.` before
customer queries or effects. The read-receipt guards sit outside the legacy broad
exception handlers, so the denial cannot be swallowed.

`message.auto_link_reference`, `last_message`, and `on_message_status_change`
quietly return in native/partial mode. They do not perform phone-based identity
adoption, maintain the old contact/last-message cache, or publish old content/phone/
reference notifications. The WhatsApp Message and core receipt ingestion continue.

No internal team ChatRoom server branch exists in this fork: the UI's “private
room” action calls customer `contacts.create`. This change does not alter another
optional `chat` app. Existing System Manager DocPerm on historical WhatsApp Contact
records and the internal Contact name-sync hook are unchanged; no generic record
permission was broadened. Cross-app legacy endpoints remain their owners' scope.

## Verification and limits

- 14 standalone Python tests passed: ready/missing/old/partial/broken footprint,
  physical schema checks, config short-circuit, every guarded customer branch,
  quiet hooks, and representative unchanged legacy reads/sends/status behavior.
- 10 branches of the actual unchanged JS bundle passed in a Node VM with DOM and
  transport doubles. Disabled modes create no UI/socket/customer list; historical
  enabled Desk/guest branches still initialize.
- Python syntax and `git diff --check` passed.
- Actual read-only probe on `meta-reliability-test-20260910.lab.xoloitzcuintles.com`
  proved native fixed exports, Page and physical schema available; the final helper
  returned `native`, blocked the legacy guard and disabled legacy hooks. HTTP/SMTP
  and commits were blocked. The helper source hash is retained in the probe log.

The optional `whatsapp_chat` app is **not installed** on that isolated site. These
checks do not claim an installed-app migration or real browser injection test.
No customer/provider request was made. Evidence: `native-workspace-tests.log` and
`native-workspace-core-probe.log` in this directory.

## Final files

- `whatsapp_chat/api/config.py`
- `whatsapp_chat/api/native_workspace.py` (new)
- `whatsapp_chat/api/message.py`
- `whatsapp_chat/api/contacts.py`
- `whatsapp_chat/api/deal_contacts.py`
- `whatsapp_chat/tests/__init__.py` (new)
- `whatsapp_chat/tests/test_native_workspace.py` (new)
- `whatsapp_chat/tests/test_native_workspace_bundle.js` (new)
- This report and the two named evidence logs.
