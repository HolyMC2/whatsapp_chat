"""The legacy guest branch of `api/config.settings` must never 500.

`settings()` is `@frappe.whitelist(allow_guest=True)` and its guest branch called
a `validate_token` that was defined NOWHERE in this repo - not a lost import, a
call to a function that only ever existed in the upstream `chat` app. The name
has been undefined since the app's first commit (`cfce3f7`, 2024-02-07), so an
unauthenticated GET of `whatsapp_chat.api.config.settings` was a guaranteed 500
on any site that reaches that branch.

Which sites reach it: `workspace_state() == "legacy"`, i.e. the crm app ABSENT
(or installed with zero native footprint). With crm installed and native,
`get_chat_settings()` answers `enable_chat: False` and `settings()` returns
first. So "crm is not installed" is the condition that REACHES this code, never
a reason to refuse it - and crm has no token validator to borrow either.

Standalone boundary tests, like test_native_workspace.py: no installed app, no
site and no customer data required.
"""

import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


API = Path(__file__).resolve().parents[1] / "api"


class FakeLogger:
    """A logger at the level Frappe actually hands out on this estate.

    `default_log_level` is ERROR unless `DEV_SERVER` is set or `log_level` is in
    common_site_config.json, and neither is true on the lab or on cell-0. A
    `.warning()` on a freshly created app logger therefore writes ZERO bytes -
    measured on the lab bench, 2026-09-12, on the first version of this
    breadcrumb. Starting the fake at ERROR is what makes that visible here.
    """

    def __init__(self):
        self.level = logging.ERROR
        self.lines = []

    def setLevel(self, level):  # noqa: N802 - logging.Logger's own spelling
        self.level = level

    def warning(self, message):
        self.lines.append(message)


class GuestTokenTests(unittest.TestCase):
    def setUp(self):
        self.logger = FakeLogger()
        self.frappe = ModuleType("frappe")
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.PermissionError = type("PermissionError", (Exception,), {})
        self.frappe.db = Mock()
        self.frappe.get_installed_apps = Mock(return_value=["frappe"])
        self.frappe.get_doc = Mock()
        self.frappe.get_all = Mock()
        self.frappe.log_error = Mock()
        self.frappe.get_hooks = Mock(return_value=[])
        self.frappe.conf = SimpleNamespace(socketio_port=9000)
        # A Guest: no `user_type` in session data, so `is_admin` is False and the
        # token branch is the one that runs.
        self.frappe.session = SimpleNamespace(user="Guest", data={})
        self.frappe.local = SimpleNamespace(site="fictional.example.invalid")
        self.frappe.logger = Mock(return_value=self.logger)
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        for name in ("native_workspace", "config"):
            fullname = "whatsapp_chat.api." + name
            spec = importlib.util.spec_from_file_location(fullname, API / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[fullname] = module
            spec.loader.exec_module(module)
            setattr(self, name, module)
        self.config._NO_TOKEN_VALIDATOR_LOGGED = False

    def _install_validator(self, validator):
        """Put a `chat` app on the site whose module exposes `validate_token`."""
        self.frappe.get_installed_apps.return_value = ["frappe", "chat"]
        module = SimpleNamespace(validate_token=validator)
        patcher = patch.object(
            self.config,
            "import_module",
            side_effect=lambda name: module if name == "chat.utils" else self.fail(f"tried {name}"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return module

    # ------------------------------------------------------- the regression

    def test_the_guest_branch_answers_instead_of_raising_name_error(self):
        """The bug itself: crm absent, a Guest, a token - this used to 500."""
        result = self.config.settings("a-stale-guest-token")

        self.assertTrue(result["enable_chat"])
        self.assertEqual(result["user"], "Guest")
        self.assertIs(result["is_verified"], False)
        self.assertNotIn("room", result)

    def test_the_everyday_first_visit_sends_an_empty_token(self):
        """`localStorage.getItem('guest_token') || ''` - the common case.

        This is why the guard degrades instead of refusing: a permission error on
        an unverifiable token would 403 every first-time visitor of the widget.
        """
        result = self.config.settings("")

        self.assertTrue(result["enable_chat"])
        self.assertIs(result["is_verified"], False)
        self.assertEqual(self.logger.lines, [], "an empty token is normal, not a misconfiguration")

    def test_no_verifier_installed_leaves_one_breadcrumb_per_process(self):
        self.config.settings("a-stale-guest-token")
        self.config.settings("another-stale-token")

        self.assertEqual(len(self.logger.lines), 1)
        self.assertEqual(self.frappe.logger.call_args.args, ("whatsapp_chat",))
        entry = json.loads(self.logger.lines[0])
        self.assertEqual(entry["scope"], "guest_token.no_verifier")
        self.assertEqual(entry["app"], "whatsapp_chat")
        self.assertEqual(entry["site"], "fictional.example.invalid")
        self.assertNotIn("stale", self.logger.lines[0], "no token material in the log line")

    def test_the_breadcrumb_raises_a_dark_logger_to_info(self):
        """Frappe hands out app loggers at ERROR here, so a warning writes 0 bytes.

        This is the `mp_point` finding of the same audit: a fresh logger name is
        created at `default_log_level`, which is ERROR while `DEV_SERVER` is unset
        and `log_level` is absent from common_site_config.json.
        """
        self.assertEqual(self.logger.level, logging.ERROR)

        self.config.settings("a-stale-guest-token")

        self.assertEqual(self.logger.level, logging.INFO)

    def test_nothing_about_the_failure_reaches_the_guest(self):
        """Guest endpoint: the body says not verified and nothing else."""
        result = self.config.settings("a-stale-guest-token")

        self.assertNotIn("room", result)
        self.assertEqual(result["user_email"], "Guest")
        for value in result.values():
            self.assertNotIn("chat.utils", str(value))
            self.assertNotIn("validator", str(value))

    # ------------------------------------------- delegation to the `chat` app

    def test_a_verified_token_opens_its_room(self):
        self._install_validator(lambda token: (True, {"room": "room-1", "email": "guest@example.invalid"}))

        result = self.config.settings("a-good-token")

        self.assertIs(result["is_verified"], True)
        self.assertEqual(result["room"], "room-1")
        self.assertEqual(result["user_email"], "guest@example.invalid")
        self.assertEqual(self.logger.lines, [])

    def test_a_wrong_token_is_not_verified(self):
        self._install_validator(lambda token: (False, None))

        result = self.config.settings("a-wrong-token")

        self.assertIs(result["is_verified"], False)
        self.assertNotIn("room", result)

    def test_a_verifier_that_raises_did_not_verify(self):
        def _explode(token):
            raise RuntimeError("private diagnostic")

        self._install_validator(_explode)

        result = self.config.settings("a-token")

        self.assertIs(result["is_verified"], False)
        for value in result.values():
            self.assertNotIn("private diagnostic", str(value))

    def test_a_malformed_verifier_answer_is_not_verified(self):
        for answer in (
            (True, None),
            (True, {}),
            (True, {"room": "room-1"}),
            (True, {"email": "guest@example.invalid"}),
            ("yes", {"room": "room-1", "email": "guest@example.invalid"}),
        ):
            with self.subTest(answer=answer):
                self.config._NO_TOKEN_VALIDATOR_LOGGED = False
                with patch.object(self.config, "_legacy_token_validator", return_value=lambda _t: answer):
                    result = self.config.settings("a-token")
                self.assertIs(result["is_verified"], False)
                self.assertNotIn("room", result)

    def test_an_absent_chat_app_is_never_imported(self):
        with patch.object(self.config, "import_module", side_effect=AssertionError("must not import")) as imported:
            result = self.config.settings("a-token")

        imported.assert_not_called()
        self.assertIs(result["is_verified"], False)

    # ------------------------------------------------ crm present: no branch

    def test_native_crm_returns_before_any_token_work(self):
        """With crm native the widget is off, so the token branch is dead code."""
        self.frappe.get_installed_apps.return_value = ["frappe", "crm"]
        self.frappe.db.exists.return_value = True
        self.frappe.db.table_exists.return_value = True
        self.frappe.db.get_table_columns.side_effect = lambda name: list(
            self.native_workspace._SCHEMAS[name]
        )
        with patch.object(
            self.native_workspace,
            "import_module",
            side_effect=lambda path: SimpleNamespace(
                **{name: Mock() for name in self.native_workspace._APIS[path]}
            ),
        ):
            with patch.object(self.config, "validate_token", side_effect=AssertionError("reached")):
                result = self.config.settings("a-token")

        self.assertFalse(result["enable_chat"])
        self.assertEqual(result["customer_workspace_status"], "native")
        self.assertNotIn("is_verified", result)


if __name__ == "__main__":
    unittest.main()
