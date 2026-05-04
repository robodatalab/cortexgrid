from __future__ import annotations

import unittest
import uuid

import cortexflow
from playwright.sync_api import sync_playwright


UI_URL = "https://cortexflow.robodatalab.com"


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


class TestExperimentListing(unittest.TestCase):
    def setUp(self) -> None:
        cortexflow.Experiment.close()
        self.addCleanup(cortexflow.Experiment.close)
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = self.pw.chromium.launch()
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page()

    def test_new_experiment_appears_while_ui_is_open(self) -> None:
        self.page.goto(UI_URL)
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        self.page.get_by_text(name).wait_for(state="visible", timeout=30_000)
