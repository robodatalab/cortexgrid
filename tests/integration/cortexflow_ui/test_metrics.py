from __future__ import annotations

import uuid

import cortexflow
from playwright.sync_api import expect

from tests.integration.cortexflow_ui._base import get_test_ui_url, UITestCase


def _experiment_name(test: UITestCase) -> str:
    return f"it-{test._testMethodName}-{uuid.uuid4().hex[:8]}"


class TestMetricsView(UITestCase):
    def test_each_logged_metric_renders_its_own_chart(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()
        cortexflow.log_metric("loss", 0.5, step=0)
        cortexflow.log_metric("accuracy", 0.9, step=0)
        cortexflow.log_metric("lr", 0.01, step=0)

        self.page.goto(get_test_ui_url())
        tree = self.page.locator(".experiment-tree__list")
        tree.get_by_text(name).click()
        tree.get_by_text(run_name).click()

        titles = self.page.locator(".run-dashboard__chart-title")
        expect(titles.get_by_text("loss", exact=True)).to_be_visible(timeout=15_000)
        expect(titles.get_by_text("accuracy", exact=True)).to_be_visible(timeout=15_000)
        expect(titles.get_by_text("lr", exact=True)).to_be_visible(timeout=15_000)

    def test_metric_logged_after_dashboard_open_appears_live(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()

        self.page.goto(get_test_ui_url())
        tree = self.page.locator(".experiment-tree__list")
        tree.get_by_text(name).click()
        tree.get_by_text(run_name).click()

        titles = self.page.locator(".run-dashboard__chart-title")
        expect(titles.get_by_text("loss", exact=True)).not_to_be_visible()

        cortexflow.log_metric("loss", 0.5, step=0)
        expect(titles.get_by_text("loss", exact=True)).to_be_visible(timeout=15_000)

    def test_metric_logged_by_one_user_appears_for_another_viewer(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()

        viewer = self.new_user()
        viewer.goto(get_test_ui_url())
        viewer_tree = viewer.locator(".experiment-tree__list")
        viewer_tree.get_by_text(name).click()
        viewer_tree.get_by_text(run_name).click()

        viewer_titles = viewer.locator(".run-dashboard__chart-title")
        expect(viewer_titles.get_by_text("loss", exact=True)).not_to_be_visible()

        cortexflow.log_metric("loss", 0.5, step=0)
        expect(viewer_titles.get_by_text("loss", exact=True)).to_be_visible(
            timeout=15_000
        )
