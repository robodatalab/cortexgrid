from __future__ import annotations

import os
import unittest

import cortexflow
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright


def get_test_ui_url() -> str:
    load_dotenv()
    return os.environ["CORTEXFLOW_UI_URL"]


class UITestCase(unittest.TestCase):
    page: Page

    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = self.pw.chromium.launch()
        self.addCleanup(self.browser.close)
        self.page = self.new_user()

    def new_user(self) -> Page:
        """Open an isolated browser context (separate cookies/storage) — simulates a different user."""
        ctx = self.browser.new_context()
        self.addCleanup(ctx.close)
        return ctx.new_page()
