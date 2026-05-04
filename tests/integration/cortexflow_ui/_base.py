from __future__ import annotations

import unittest

import cortexflow
from playwright.sync_api import Page, sync_playwright


UI_URL = "https://cortexflow.robodatalab.com"


class UITestCase(unittest.TestCase):
    page: Page

    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = self.pw.chromium.launch()
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page()
