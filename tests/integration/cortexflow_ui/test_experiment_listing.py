from __future__ import annotations

import uuid

import cortexflow
from playwright.sync_api import expect

from tests.integration.cortexflow_ui._base import UI_URL, UITestCase


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


def _noop_job() -> None:
    cortexflow.log_metric("ran", 1.0)


class TestExperimentListing(UITestCase):
    def test_experiment_appears_then_disappears_after_delete(self) -> None:
        self.page.goto(UI_URL)
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        tree = self.page.locator(".experiment-tree__list")

        expect(self.page.get_by_text("Experiments", exact=True)).to_be_visible()
        expect(tree.get_by_text(name)).not_to_be_visible()

        cortexflow.Experiment.init(name)
        expect(tree.get_by_text(name)).to_be_visible(timeout=30_000)

        cortexflow.delete_experiment(name)
        expect(tree.get_by_text(name)).not_to_be_visible(timeout=30_000)

    def test_independent_creates_and_deletes_visible_to_both_users(self) -> None:
        exp1 = _experiment_name()
        exp2 = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, exp1)
        self.addCleanup(cortexflow.delete_experiment, exp2)

        a = self.page
        b = self.new_user()
        a.goto(UI_URL)
        b.goto(UI_URL)
        a_tree = a.locator(".experiment-tree__list")
        b_tree = b.locator(".experiment-tree__list")

        cortexflow.Experiment.init(exp1)
        cortexflow.Experiment.close()
        cortexflow.Experiment.init(exp2)

        expect(a_tree.get_by_text(exp1)).to_be_visible(timeout=30_000)
        expect(a_tree.get_by_text(exp2)).to_be_visible(timeout=30_000)
        expect(b_tree.get_by_text(exp1)).to_be_visible(timeout=30_000)
        expect(b_tree.get_by_text(exp2)).to_be_visible(timeout=30_000)

        cortexflow.delete_experiment(exp1)
        expect(a_tree.get_by_text(exp1)).not_to_be_visible(timeout=30_000)
        expect(b_tree.get_by_text(exp1)).not_to_be_visible(timeout=30_000)
        expect(a_tree.get_by_text(exp2)).to_be_visible()
        expect(b_tree.get_by_text(exp2)).to_be_visible()

    def test_run_and_job_appear_then_disappear_after_delete(self) -> None:
        self.page.goto(UI_URL)
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        tree = self.page.locator(".experiment-tree__list")

        expect(self.page.get_by_text("Experiments", exact=True)).to_be_visible()

        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()

        expect(tree.get_by_text(name)).to_be_visible(timeout=30_000)
        tree.get_by_text(name).click()
        expect(tree.get_by_text(run_name)).to_be_visible(timeout=30_000)

        tree.get_by_text(run_name).click()
        expect(tree.get_by_text("No jobs")).to_be_visible(timeout=10_000)

        job_id = cortexflow.remote(_noop_job)
        expect(tree.get_by_text(job_id)).to_be_visible(timeout=30_000)
        expect(tree.get_by_text("No jobs")).not_to_be_visible()

        cortexflow.delete_experiment(name)
        expect(tree.get_by_text(name)).not_to_be_visible(timeout=30_000)
