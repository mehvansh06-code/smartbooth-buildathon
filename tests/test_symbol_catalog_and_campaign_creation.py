"""
tests/test_symbol_catalog_and_campaign_creation.py
Verification suite for Rajasthan SEC 40 Sarpanch symbols, 40 Ward Panch symbols,
popular free symbols, and their integration into the campaign creation system.
"""

import os
import unittest
from jinja2 import Environment, FileSystemLoader
from flask import Flask

from electionapp.symbol_catalog import (
    SARPANCH_SYMBOLS,
    WARD_PANCH_SYMBOLS,
    POPULAR_FREE_SYMBOLS,
    POLITICAL_PARTY_SYMBOLS,
    COMPREHENSIVE_SYMBOL_MAP,
)
from electionapp.campaign_manager import _get_preset_symbol_key, _resolve_preset_symbol_path
from electionapp.config import PLATFORM_ROOT
from electionapp.blueprints.admin import admin_bp


class TestSymbolCatalog(unittest.TestCase):
    """Validates SEC Rajasthan symbol catalogs, asset existence, and normalization mappings."""

    def test_sarpanch_symbols_count_and_schema(self):
        self.assertEqual(len(SARPANCH_SYMBOLS), 40, "Official SEC Sarpanch catalog must contain exactly 40 symbols")
        for sym in SARPANCH_SYMBOLS:
            self.assertIn("id", sym)
            self.assertIn("name_hi", sym)
            self.assertIn("en_name", sym)
            self.assertIn("file", sym)
            self.assertIn("keywords", sym)
            self.assertTrue(sym["name_hi"].strip())
            self.assertTrue(sym["file"].endswith(".png"))

    def test_sarpanch_symbols_assets_exist(self):
        symbols_dir = os.path.join(PLATFORM_ROOT, "static", "symbols")
        for sym in SARPANCH_SYMBOLS:
            asset_path = os.path.join(symbols_dir, sym["file"])
            self.assertTrue(os.path.exists(asset_path), f"Sarpanch symbol image missing: {sym['file']}")

    def test_ward_panch_symbols_count_and_schema(self):
        self.assertEqual(len(WARD_PANCH_SYMBOLS), 40, "Official SEC Ward Panch catalog must contain exactly 40 symbols")
        for sym in WARD_PANCH_SYMBOLS:
            self.assertIn("id", sym)
            self.assertIn("name_hi", sym)
            self.assertIn("en_name", sym)
            self.assertIn("file", sym)
            self.assertIn("keywords", sym)
            self.assertTrue(sym["name_hi"].strip())
            self.assertTrue(sym["file"].endswith(".png"))

    def test_ward_panch_symbols_assets_exist(self):
        symbols_dir = os.path.join(PLATFORM_ROOT, "static", "symbols")
        for sym in WARD_PANCH_SYMBOLS:
            asset_path = os.path.join(symbols_dir, sym["file"])
            self.assertTrue(os.path.exists(asset_path), f"Ward Panch symbol image missing: {sym['file']}")

    def test_party_symbols_assets_exist(self):
        symbols_dir = os.path.join(PLATFORM_ROOT, "static", "symbols")
        for sym in POLITICAL_PARTY_SYMBOLS:
            asset_path = os.path.join(symbols_dir, sym["file"])
            self.assertTrue(os.path.exists(asset_path), f"Party symbol image missing: {sym['file']}")

    def test_popular_symbols_assets_exist(self):
        symbols_dir = os.path.join(PLATFORM_ROOT, "static", "symbols")
        for sym in POPULAR_FREE_SYMBOLS:
            asset_path = os.path.join(symbols_dir, sym["file"])
            self.assertTrue(os.path.exists(asset_path), f"Popular free symbol image missing: {sym['file']}")

    def test_normalization_and_resolution(self):
        # Hindi names resolve to normalized key
        self.assertEqual(_get_preset_symbol_key("प्रेस"), "press_iron")
        self.assertEqual(_get_preset_symbol_key("स्लेट"), "slate")
        self.assertEqual(_get_preset_symbol_key("कटहल"), "jackfruit")
        self.assertEqual(_get_preset_symbol_key("अलमारी"), "almirah")
        self.assertEqual(_get_preset_symbol_key("ट्रैक्टर"), "tractor")
        self.assertEqual(_get_preset_symbol_key("हैंडपंप"), "handpump")

        # English keys resolve
        self.assertEqual(_get_preset_symbol_key("press_iron"), "press_iron")
        self.assertEqual(_get_preset_symbol_key("press_iron.png"), "press_iron")
        self.assertEqual(_get_preset_symbol_key("/static/symbols/press_iron.png"), "press_iron")

        # Symbol path resolution
        path = _resolve_preset_symbol_path("प्रेस")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.endswith("press_iron.png"))


class TestCampaignCreatePageTemplate(unittest.TestCase):
    """Validates campaign creation template rendering with 40 Sarpanch symbols, 40 Ward Panch symbols, tabs and search."""

    def test_template_renders_all_symbols(self):
        env = Environment(
            loader=FileSystemLoader(os.path.join(PLATFORM_ROOT, "templates")),
            autoescape=True
        )
        template = env.get_template("admin/campaign_create_page.html")
        rendered = template.render(
            edit_slug=None,
            is_reseller=False,
            reseller_credits=0,
            session={"role": "super_admin"},
            sarpanch_symbols=SARPANCH_SYMBOLS,
            ward_panch_symbols=WARD_PANCH_SYMBOLS,
            popular_symbols=POPULAR_FREE_SYMBOLS,
            party_symbols=POLITICAL_PARTY_SYMBOLS,
        )

        # Category tabs
        self.assertIn("tabSymSarpanch", rendered)
        self.assertIn("tabSymWardPanch", rendered)
        self.assertIn("tabSymPopular", rendered)
        self.assertIn("tabSymAll", rendered)

        # Search box & counter badge
        self.assertIn("secSymbolSearch", rendered)
        self.assertIn("symbolCountBadge", rendered)

        # Sarpanch 40 symbols rendered
        for sym in SARPANCH_SYMBOLS:
            self.assertIn(sym["file"], rendered, f"Sarpanch symbol {sym['file']} should be rendered in template")
            self.assertIn(f"#{sym['id']}", rendered, f"Sarpanch symbol #{sym['id']} badge should be rendered")

        # Ward Panch 40 symbols rendered
        for sym in WARD_PANCH_SYMBOLS:
            self.assertIn(sym["file"], rendered, f"Ward Panch symbol {sym['file']} should be rendered in template")

        # Popular symbols rendered
        for sym in POPULAR_FREE_SYMBOLS:
            self.assertIn(sym["file"], rendered, f"Popular symbol {sym['file']} should be rendered in template")

        # JavaScript controllers present
        self.assertIn("setSymbolCategory", rendered)
        self.assertIn("filterSecSymbols", rendered)
        self.assertIn("selectSymbolPreset", rendered)


class TestCatalogApiEndpoint(unittest.TestCase):
    """Validates /api/catalog/symbols endpoint response and payload schema."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.secret_key = "test_key"
        self.app.register_blueprint(admin_bp)
        self.client = self.app.test_client()

    def test_api_catalog_symbols(self):
        res = self.client.get("/api/catalog/symbols?type=PANCHAYAT")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["sarpanch_symbols"]), 40)
        self.assertEqual(len(data["ward_panch_symbols"]), 40)
        self.assertGreaterEqual(len(data["popular_symbols"]), 15)
        self.assertEqual(len(data["party_symbols"]), 8)


if __name__ == "__main__":
    unittest.main()
