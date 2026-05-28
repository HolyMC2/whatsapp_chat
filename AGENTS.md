# whatsapp_chat-fork — Desk WhatsApp Chat UI (HolyMC2 fork)

> ⚠ **Multi-agent stomping safeguard**: before any edit/restart, read [muelle/AGENTS.md → Coordination](../muelle/AGENTS.md#coordination--multi-agent-freshness-read-before-any-write). Use `bash ../muelle/scripts/muelle-restart.sh <svc> --reason "..."` not raw `docker compose restart`. Memory entries: `feedback_agent_freshness_protocol`, `feedback_restart_coordination`.

Fork of `shridarpatil/whatsapp_chat`, branch `doco-customizations`. The upstream UI ships a Desk floating chat bubble that mostly worked on Frappe v13-v14 but had several v16 incompatibilities + missing features for Marco's workflow. This fork patches all of them.

Companion to [`crm/`](../crm/AGENTS.md) (which carries the per-Deal WhatsApp tab) and the upstream [`frappe_whatsapp`](https://github.com/shridarpatil/frappe_whatsapp) connector (provides the Meta API client + WhatsApp Message doctype). See [`muelle-host/AGENTS.md`](../AGENTS.md) for the broader stack map.

## What this fork patches (vs upstream)

| Area | File | What changed |
|---|---|---|
| Desk chat bubble visibility | `whatsapp_chat/public/js/components/chat_bubble.js:14` | Removed `d-none` gate that hid the bubble in Desk in v16 (navbar fallback selector didn't match v16 DOM) |
| New `WhatsApp Contact` defaults | `whatsapp_chat/api/message.py:last_message` | Auto-assigns `email` to first System Manager so realtime events have a recipient; resolves real `contact_name` via `crm.integrations.api.get_contact_by_phone_number` |
| Phone normalization | `whatsapp_chat/api/message.py:_normalize_mx_phone` | Coerces `+526691530561`, `6691530561`, `5216691530561` all to canonical `526691530561` so chat history groups under one identity |
| Auto-heal existing contact name | `whatsapp_chat/api/message.py:last_message` | When existing contact's `contact_name` still phone-shaped, re-resolves on every message tick — fixes contacts created before a matching Frappe Contact existed |
| Last-10-digit Contact lookup fallback | `whatsapp_chat/api/message.py:_resolve_contact_name` | CRM's `get_contact_by_phone_number` is too strict (rejects MX 521 legacy prefix). Direct Contact + Contact Phone fallback survives all permutations |
| Frappe Contact → WhatsApp Contact name sync | `whatsapp_chat/api/contact_sync.py` + hooks | New `Contact.on_update` hook: when operator fills in a real name on Frappe Contact, every matching WhatsApp Contact updates display name |
| WhatsApp Message → Deal/Lead auto-link | `whatsapp_chat/api/message.py:auto_link_reference` + hooks `WhatsApp Message.before_insert` | Stamps `reference_doctype` + `reference_name` to latest-modified matching CRM Deal/Lead so the Deal's WhatsApp tab scopes correctly |
| Deal contact list API | `whatsapp_chat/api/deal_contacts.py` | New whitelisted `get_deal_whatsapp_contacts` returns one entry per Contact on a Deal — drives the multi-Contact tab strip in fcrm |
| Bubble status indicators | `whatsapp_chat/public/js/components/chat_space.js:make_message` | WhatsApp-style ticks next to message time: ✓ sent / ✓✓ delivered (grey) / ✓✓ read (blue) / ⚠ failed (red) |
| Bubble header phone | `whatsapp_chat/public/js/components/chat_space.js:setup_header` | Show formatted phone under contact name. Uses `.chat-profile-phone` class (NOT `.chat-profile-status` — that's toggled by the typing-listener which would hide the phone) |
| Chat list row phone | `whatsapp_chat/public/js/components/chat_room.js:setup` | Small monospace phone between name and last-message preview |
| Optimistic send re-fetch | `whatsapp_chat/public/js/components/chat_space.js:handle_send_message` | After every send, schedule 4s `refresh_messages()` so the status indicator (✓✓ or ⚠ failed) appears without close+reopen |
| `get_all` returns status | `whatsapp_chat/api/message.py:get_all` | Now selects `name`, `type`, `status` so the bubble can render delivery indicators |

All patches gated by being-on-this-fork; safe to re-pull upstream and reapply if needed.

## Repo layout

```
whatsapp_chat-fork/
  whatsapp_chat/
    hooks.py                              ← doc_events: WhatsApp Message.before_insert + after_insert, Contact.on_update
    api/
      message.py                          ← last_message, get_all, _normalize_mx_phone, _resolve_contact_name, auto_link_reference, _looks_like_phone, _default_assignee_email
      contact_sync.py                     ← Contact.on_update → WhatsApp Contact name sync
      deal_contacts.py                    ← get_deal_whatsapp_contacts (whitelisted, used by fcrm tab strip)
      config.py                           ← settings endpoint (chat_status, enable_chat — both hard-coded True)
    public/
      js/components/
        chat_space.js                     ← chat room UI (messages, header, send)
        chat_room.js                      ← chat list row (name + phone + last_message preview)
        chat_bubble.js                    ← floating bubble entry
        chat_list.js                      ← chat list pane
      sounds/                             ← notification audio
    whatsapp_chat/doctype/
      whatsapp_contact/                   ← persisted chat list entry
```

## Key conventions

- **Phone identity = digits-only** (`526691530561`). Different from Twilio voice which uses `+E.164` (`+526691530561`). Don't unify — separate protocols, separate stores. CRM Deal `mobile_no` is the bridge with no enforced format.
- **`WhatsApp Contact.email`** routes realtime events via `frappe.publish_realtime(..., user=email)`. Without it set, events are silently dropped.
- **`.chat-profile-status` class** is toggled by an inherited typing listener (Meta doesn't actually expose typing events; the toggle is dead code that runs on stale `is_typing` socket pushes). Don't put persistent content in this class — use `.chat-profile-phone` (new) instead.
- **Optimistic message bubbles** carry no status indicator initially. Server-side status (delivered/failed) propagates either via the 4s re-fetch or by close+reopen of the bubble.

## Companion repos

- [`crm/`](../crm/AGENTS.md) — fcrm's WhatsApp tab consumes `whatsapp_chat.api.deal_contacts.get_deal_whatsapp_contacts`. Auto-link hook here, multi-Contact tabs there.
- [`doco_meta_catalog/`](../doco_meta_catalog/AGENTS.md) — sibling: ERPNext Item → Meta Commerce Catalog. Will also exchange WhatsApp product messages via this connector once the catalog ships.
- Upstream: `shridarpatil/frappe_whatsapp` (Meta connector + `WhatsApp Message` doctype) — NOT forked, kept upstream
- Upstream: `shridarpatil/whatsapp_chat` — what this fork came from

## Memory (read-only)

- `~/.claude/projects/-home-holymc2/memory/project_doco_whatsapp.md` — WhatsApp stack
- `~/.claude/projects/-home-holymc2/memory/project_chatwoot_dockervm.md` — Chatwoot pairing plan

## Deploy

```bash
# Lab
cd ~/muelle-host/muelle
# Source-only changes (Python or component JS):
docker compose cp ~/muelle-host/whatsapp_chat-fork/whatsapp_chat/api/message.py \
  backend:/home/frappe/frappe-bench/apps/whatsapp_chat/whatsapp_chat/api/message.py
docker compose exec -T backend bash -c "cd /home/frappe/frappe-bench && bench build --app whatsapp_chat"
docker compose restart backend queue-short queue-long

# Prod
ssh contavm 'cd ~/muelle && docker compose cp /tmp/file backend:/path && \
  docker compose exec -T backend bash -c "bench build --app whatsapp_chat" && \
  docker compose restart backend queue-short queue-long'
```

The prod `apps/whatsapp_chat/` install is **missing `.git`** (per session 2026-05-24 observation) — direct file `cp` is the current update path, NOT `git pull`. Worth re-cloning from fork on prod for proper `git fetch + reset` workflow.

## Conventions specific to this fork

- Commit with conventional prefix: `feat:`, `fix:`, `chore:`. No scope prefix needed (single-app repo).
- Keep upstream-friendly: patches should be reviewable for upstreaming if Marco ever wants to PR back to shridarpatil.
- DOM class names (`chat-profile-*`, `message-time`, `msg-status-tag`) MUST stay stable — upstream code path (typing listener etc.) targets some of them.

## Recent commits (this session)

| Commit | What |
|---|---|
| `9fc3c08` | Show chat bubble in Desk (v16 navbar selector dead) + auto-assign + name resolve + MX phone normalize |
| `398a884` | Heal contact name from Frappe Contact + Contact.on_update sync |
| `5f38cfb` | Last-10-digit Contact lookup fallback |
| `d8ff06f` | Link WhatsApp Message to latest Deal/Lead + deal_contacts API |
| `0ae00d1` | Phone shown under header |
| `94be7c6` | Per-message status indicators (✓ / ✓✓ / ⚠) + phone class collision fix |
| `5b75116` | Phone in chat-list rows |
| `a30a6a9` | 4-second re-fetch after send so status appears |

---

*Living doc. Cross-link aggressively when you patch behavior.*
