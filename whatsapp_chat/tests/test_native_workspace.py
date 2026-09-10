"""Standalone boundary tests; no installed whatsapp_chat app or customer data required."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


API = Path(__file__).resolve().parents[1] / "api"


class NativeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.frappe = ModuleType("frappe")
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.PermissionError = type("PermissionError", (Exception,), {})
        self.frappe.db = Mock()
        self.frappe.get_installed_apps = Mock(return_value=["frappe", "crm"])
        self.frappe.get_doc = Mock()
        self.frappe.get_all = Mock()
        self.frappe.has_permission = Mock()
        self.frappe.publish_realtime = Mock()
        self.frappe.log_error = Mock()
        self.frappe.get_hooks = Mock(return_value=[])
        self.frappe.conf = SimpleNamespace(socketio_port=9000)
        self.frappe.session = SimpleNamespace(user="fictional@example.invalid", data={"user_type": "System User"})
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        for name in ("native_workspace", "config", "message", "contacts", "deal_contacts"):
            fullname = "whatsapp_chat.api." + name
            spec = importlib.util.spec_from_file_location(fullname, API / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[fullname] = module
            spec.loader.exec_module(module)
            setattr(self, name, module)
        self.native = self.native_workspace
        self.imports = patch.object(self.native, "import_module", side_effect=self._module)
        self.imports.start()
        self.addCleanup(self.imports.stop)
        self.frappe.db.exists.return_value = True
        self.frappe.db.table_exists.return_value = True
        self.frappe.db.get_table_columns.side_effect = lambda name: list(self.native._SCHEMAS[name])

    def _module(self, path):
        return SimpleNamespace(**{name: Mock() for name in self.native._APIS[path]})

    @staticmethod
    def _missing_module(path):
        raise ModuleNotFoundError("service absent", name=path)

    def test_ready_requires_actual_page_tables_columns_and_fixed_exports(self):
        self.assertEqual(self.native.workspace_state(), "native")
        self.assertEqual(set(self.native.import_module.call_args_list[i].args[0] for i in range(3)), set(self.native._APIS))
        for name in self.native._SCHEMAS:
            self.frappe.db.table_exists.assert_any_call(name)
            self.frappe.db.get_table_columns.assert_any_call(name)
        self.frappe.get_doc.assert_not_called()
        self.frappe.db.sql.assert_not_called()

    def test_no_crm_preserves_legacy_without_importing_or_probing_native(self):
        self.frappe.get_installed_apps.return_value = ["frappe"]
        self.assertEqual(self.native.workspace_state(), "legacy")
        self.assertEqual(self.config.get_chat_settings(), {"enable_chat": True, "chat_status": "Online"})
        self.native.import_module.assert_not_called()
        self.frappe.db.exists.assert_not_called()

    def test_truly_old_crm_zero_footprint_preserves_legacy(self):
        self.frappe.db.exists.return_value = False
        self.frappe.db.table_exists.return_value = False
        self.native.import_module.side_effect = self._missing_module
        self.assertEqual(self.native.workspace_state(), "legacy")
        self.native.require_legacy_customer_access()
        self.assertTrue(self.native.legacy_customer_hooks_enabled())

    def test_orphaned_native_table_is_not_zero_footprint(self):
        self.frappe.db.exists.return_value = False
        self.native.import_module.side_effect = self._missing_module
        for present in self.native._SCHEMAS:
            with self.subTest(present=present):
                self.frappe.db.table_exists.side_effect = lambda name: name == present
                self.assertEqual(self.native.workspace_state(), "unavailable")

    def test_each_native_metadata_footprint_blocks_legacy_when_modules_absent(self):
        self.native.import_module.side_effect = self._missing_module
        self.frappe.db.table_exists.return_value = False
        for key in [("Page", "customer-conversations"), *[("DocType", name) for name in self.native._SCHEMAS]]:
            with self.subTest(key=key):
                self.frappe.db.exists.side_effect = lambda dt, name: (dt, name) == key
                self.assertEqual(self.native.workspace_state(), "unavailable")
                with self.assertRaisesRegex(self.frappe.PermissionError, "^Use native Conversations\\.$"):
                    self.native.require_legacy_customer_access()

    def test_each_importable_service_alone_activates_transition(self):
        self.frappe.db.exists.return_value = False
        self.frappe.db.table_exists.return_value = False
        for present in self.native._APIS:
            with self.subTest(present=present):
                self.native.import_module.side_effect = lambda path: self._module(path) if path == present else self._missing_module(path)
                self.assertEqual(self.native.workspace_state(), "unavailable")

    def test_broken_dependency_does_not_look_like_old_crm(self):
        self.frappe.db.exists.return_value = False
        self.native.import_module.side_effect = ModuleNotFoundError("do not expose dependency detail", name="missing_dependency")
        self.assertEqual(self.native.workspace_state(), "unavailable")
        self.frappe.log_error.assert_not_called()

    def test_missing_table_column_or_callable_holds_route(self):
        self.frappe.db.table_exists.return_value = False
        self.assertEqual(self.native.workspace_state(), "unavailable")
        self.frappe.db.table_exists.return_value = True
        self.frappe.db.get_table_columns.side_effect = lambda name: []
        self.assertEqual(self.native.workspace_state(), "unavailable")
        self.frappe.db.get_table_columns.side_effect = lambda name: list(self.native._SCHEMAS[name])
        self.native.import_module.side_effect = lambda path: SimpleNamespace()
        self.assertEqual(self.native.workspace_state(), "unavailable")

    def test_failed_installed_app_or_schema_probe_is_closed(self):
        self.frappe.get_installed_apps.side_effect = RuntimeError("private diagnostic")
        self.assertEqual(self.native.workspace_state(), "unavailable")
        self.frappe.get_installed_apps.side_effect = None
        self.frappe.db.exists.side_effect = RuntimeError("private diagnostic")
        self.assertEqual(self.native.workspace_state(), "unavailable")

    def test_config_hides_both_launchers_and_exposes_only_fixed_ready_route(self):
        result = self.config.settings("untrusted guest token")
        self.assertFalse(result["enable_chat"])
        self.assertEqual(result["customer_workspace"], "/desk/customer-conversations")
        self.assertEqual(result["customer_workspace_status"], "native")
        self.frappe.db.get_value.assert_not_called()
        self.frappe.session = SimpleNamespace(user="Guest", data={})
        self.frappe.db.table_exists.return_value = False
        result = self.config.settings("untrusted guest token")
        self.assertFalse(result["enable_chat"])
        self.assertIsNone(result["customer_workspace"])
        self.assertEqual(result["customer_workspace_status"], "unavailable")
        self.assertNotIn("is_verified", result)

    def test_all_customer_branches_reject_before_customer_queries_or_effects(self):
        endpoints = (
            lambda: self.message.get_all("room", "526690000000"),
            lambda: self.message.mark_as_read("room"),
            lambda: self.message.send_whatsapp_read_receipts("room"),
            lambda: self.message.send("hello", "spoofed", "room", "526690000000"),
            lambda: self.message.send("file.pdf", "spoofed", "room", "526690000000", attachment=True),
            lambda: self.contacts.get("someone-else@example.invalid"),
            lambda: self.contacts.create("name", "526690000000", "someone-else@example.invalid"),
            lambda: self.deal_contacts.get_deal_whatsapp_contacts("CRM Deal", "fictional"),
            lambda: self.deal_contacts.get_deal_whatsapp_contacts("CRM Lead", "fictional"),
        )
        for ready in (True, False):
            self.frappe.db.table_exists.return_value = ready
            for i, endpoint in enumerate(endpoints):
                with self.subTest(ready=ready, endpoint=i):
                    with self.assertRaisesRegex(self.frappe.PermissionError, "^Use native Conversations\\.$"):
                        endpoint()
        for fn in (self.frappe.db.sql, self.frappe.db.get_value, self.frappe.db.set_value,
                   self.frappe.db.get_all, self.frappe.get_all, self.frappe.get_doc,
                   self.frappe.publish_realtime, self.frappe.log_error, self.frappe.has_permission):
            fn.assert_not_called()

    def test_retired_hooks_do_not_touch_document_or_emit_or_abort_ingestion(self):
        doc = Mock()
        for ready in (True, False):
            self.frappe.db.table_exists.return_value = ready
            self.assertIsNone(self.message.auto_link_reference(doc))
            self.assertIsNone(self.message.on_message_status_change(doc))
            self.assertIsNone(self.message.last_message(doc, "after_insert"))
        self.assertEqual(doc.mock_calls, [])
        self.frappe.db.get_value.assert_not_called()
        self.frappe.get_doc.assert_not_called()
        self.frappe.publish_realtime.assert_not_called()

    def test_absent_crm_keeps_existing_read_and_send_contract(self):
        self.frappe.get_installed_apps.return_value = ["frappe"]
        self.frappe.db.sql.return_value = [{"name": "fictional-message"}]
        self.assertEqual(self.message.get_all("room", "526690000000"), [{"name": "fictional-message"}])
        self.assertEqual(self.frappe.db.sql.call_args.args[1], {"user_no": "526690000000"})
        self.assertEqual(self.message.send("hello", "staff", "room", "526690000000"), "ok")
        self.frappe.get_doc.return_value.save.assert_called_once_with()
        self.assertEqual(self.frappe.get_doc.call_args.args[0]["message"], "hello")
        self.frappe.db.get_all.return_value = [{"name": "fictional-contact"}]
        self.assertEqual(self.contacts.get("staff@example.invalid"), [{"name": "fictional-contact"}])

    def test_absent_crm_preserves_explicit_reference_and_legacy_status_hook(self):
        self.frappe.get_installed_apps.return_value = ["frappe"]
        doc = Mock(reference_doctype="CRM Deal", reference_name="fictional")
        doc.get.side_effect = lambda key: getattr(doc, key)
        doc.has_value_changed.return_value = True
        self.message.auto_link_reference(doc)
        self.frappe.db.sql.assert_not_called()
        self.message.on_message_status_change(doc)
        self.frappe.publish_realtime.assert_called_once_with(
            "whatsapp_status", {"reference_doctype": "CRM Deal", "reference_name": "fictional"}, after_commit=True,
        )


if __name__ == "__main__":
    unittest.main()
