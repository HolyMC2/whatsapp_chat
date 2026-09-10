// Execute the unchanged production bundle's async gate, with UI/transport doubles.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../public/js/whatsapp_chat.bundle.js'), 'utf8')
  .replace(/import\s*\{[\s\S]*?\}\s*from '\.\/components';/, '');

async function check(settings, desk, expectedCreated) {
  const calls = { create: 0, socket: 0, list: 0, welcome: 0, error: 0 };
  const context = {
    frappe: { provide() {}, socketio: { async init() { calls.socket++; } } },
    localStorage: { getItem: () => null },
    console: { error() { calls.error++; } },
    get_settings: async () => settings,
    // Do not run jQuery's document-ready constructor; exercise setup_app directly.
    $: () => undefined,
    ChatList: class { render() { calls.list++; } },
    ChatWelcome: class { render() { calls.welcome++; } },
  };
  if (desk) context.frappe.desk = {};
  vm.runInNewContext(source, context, { filename: 'whatsapp_chat.bundle.js' });
  const widget = Object.create(context.frappe.Chat.prototype);
  widget.create_app = () => { calls.create++; };
  await widget.setup_app();
  assert.equal(calls.create, expectedCreated, 'create_app contains both FAB and navbar insertion');
  assert.equal(calls.socket, expectedCreated);
  assert.equal(calls.error, 0);
  if (!expectedCreated) {
    assert.equal(calls.list, 0);
    assert.equal(calls.welcome, 0);
  }
}

(async () => {
  for (const desk of [true, false]) {
    for (const is_admin of [true, false]) {
      await check({ enable_chat: false, customer_workspace: '/desk/customer-conversations', is_admin }, desk, 0);
      await check({ enable_chat: false, customer_workspace: null, is_admin }, desk, 0);
    }
  }
  await check({ enable_chat: true, is_admin: true, user_settings: {} }, true, 1);
  await check({ enable_chat: true, is_admin: false, user_settings: {} }, false, 1);
  console.log('PASS: 10 actual bundle setup branches; native/partial creates no FAB, navbar, socket or customer list.');
})().catch(error => { console.error(error); process.exitCode = 1; });
