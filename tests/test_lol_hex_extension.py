# -*- coding: utf-8 -*-
"""Regression contracts for the optional LOL Hex extension integration."""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from core.extension_manager import LolHexExtensionManager


class LolHexExtensionManagerTests(unittest.TestCase):
    def test_reads_extension_port_from_its_own_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "extensions" / "lol_hex_display"
            extension.mkdir(parents=True)
            (extension / "config.txt").write_text(
                "host=127.0.0.1\nport=8877\n", encoding="utf-8"
            )
            manager = LolHexExtensionManager(root, logging.getLogger("test"))
            self.assertEqual(manager.port, 8877)
            self.assertEqual(manager.base_url, "http://127.0.0.1:8877")

    def test_detects_an_independently_running_extension_without_claiming_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = LolHexExtensionManager(Path(directory), logging.getLogger("test"))
            manager._is_healthy = lambda: True
            state = manager.status()
            self.assertTrue(state["running"])
            self.assertTrue(state["external"])
            self.assertFalse(state["owned"])

    def test_can_stop_an_independently_running_extension_on_explicit_request(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = LolHexExtensionManager(Path(directory), logging.getLogger("test"))
            health = iter([True, False])
            calls = []
            manager._is_healthy = lambda: next(health, False)
            manager._request = lambda path, payload=None, timeout=1.0: calls.append((path, payload)) or {}
            state = manager.stop()
            self.assertEqual(calls[0], ("/api/action", {"action": "shutdown"}))
            self.assertFalse(state["running"])


class LolHexIntegrationContractTests(unittest.TestCase):
    def test_lifecycle_is_managed_by_the_main_application(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        manager = (root / "core" / "extension_manager.py").read_text(encoding="utf-8")
        page = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('from core.extension_manager import LolHexExtensionManager', main)
        self.assertIn('@app.post("/api/extensions/lol-hex/start")', main)
        self.assertIn('@app.post("/api/extensions/lol-hex/stop")', main)
        self.assertIn("LOL_HEX_EXTENSION.stop_if_owned()", main)
        self.assertIn('id="lol-hex-start"', page)
        self.assertIn('id="lol-hex-stop"', page)
        self.assertIn('id="lol-hex-module"', page)
        self.assertIn('href="/extensions/lol-hex/"', page)
        self.assertIn('env["LOL_HEX_MANAGED_BY_BILI"] = "1"', manager)

    def test_bar_and_hex_display_are_two_independent_layout_widgets(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        page = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        self.assertIn('"lol_hex_bar"', main)
        self.assertIn('"lol_hex_display"', main)
        self.assertIn('data-add="lol_hex_bar"', page)
        self.assertIn('data-add="lol_hex_display"', page)
        self.assertIn('"/extensions/lol-hex/bar"', page)
        self.assertIn('"/extensions/lol-hex/display"', page)
        self.assertIn('api("/api/extensions/lol-hex")', page)

    def test_build_packages_the_hex_application_as_a_separate_folder(self):
        root = Path(__file__).resolve().parents[1]
        build = (root / "build.bat").read_text(encoding="utf-8")
        modules = (root / ".gitmodules").read_text(encoding="utf-8")
        self.assertIn("git submodule update --init --recursive", build)
        self.assertIn("extensions\\lol_hex_display\\build.bat", build)
        self.assertIn("dist\\lol_hex_display", build)
        self.assertIn("path = extensions/lol_hex_display", modules)


if __name__ == "__main__":
    unittest.main()
