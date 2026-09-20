from __future__ import annotations

import uuid

import cortexgrid
from playwright.sync_api import expect

from tests.integration.cortexgrid_ui._base import get_test_ui_url, UITestCase


def _experiment_name(test: UITestCase) -> str:
    return f"it-{test._testMethodName}-{uuid.uuid4().hex[:8]}"


def _noop_job() -> None:
    cortexgrid.log_metric("ran", 1.0)


class TestJobsTab(UITestCase):
    def test_job_is_listed_with_the_experiment_and_run_that_own_it(self) -> None:
        """Also the guard that the tab has a backend at all: without its
        feed the table stays empty and this fails."""
        name = _experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        exp = cortexgrid.Experiment.init(name)
        run_name = exp.run_name()
        job_id = cortexgrid.remote(_noop_job).job_id

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Jobs").click()
        row = self.page.locator(".jobs-dashboard__row", has_text=job_id)

        expect(row).to_be_visible(timeout=60_000)
        expect(row).to_contain_text(name)
        expect(row).to_contain_text(run_name)

    def test_switching_every_status_filter_off_empties_the_table(self) -> None:
        """Filter by status, without betting on the status of a live job."""
        name = _experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        cortexgrid.Experiment.init(name)
        job_id = cortexgrid.remote(_noop_job).job_id

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Jobs").click()
        row = self.page.locator(".jobs-dashboard__row", has_text=job_id)
        expect(row).to_be_visible(timeout=60_000)

        filters = self.page.locator(".jobs-dashboard__filter")
        for index in range(filters.count()):
            filters.nth(index).click()

        expect(row).not_to_be_visible()
        expect(
            self.page.get_by_text("Every job is filtered out")
        ).to_be_visible()

        for index in range(filters.count()):
            filters.nth(index).click()

        expect(row).to_be_visible()

    def test_a_deleted_job_says_deleting_then_goes_for_good(self) -> None:
        """The whole path: the UI writes intent, the control plane acts."""
        name = _experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        exp = cortexgrid.Experiment.init(name)
        run_name = exp.run_name()
        job_id = cortexgrid.remote(_noop_job).job_id

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Jobs").click()
        row = self.page.locator(".jobs-dashboard__row", has_text=job_id)
        expect(row).to_be_visible(timeout=60_000)

        row.get_by_role("button", name=f"Delete job {job_id}").click()
        self.page.get_by_role("button", name="Delete", exact=True).click()

        # The request is a latch; the control plane stops the Ray attempt,
        # waits for it to settle and removes the record on a later cycle.
        expect(row.locator(".job-status--deleting")).to_be_visible(timeout=30_000)
        expect(row).not_to_be_visible(timeout=120_000)

        self.page.get_by_role("button", name="Experiments").click()
        tree = self.page.locator(".experiment-tree__list")
        tree.get_by_text(name).click()
        tree.get_by_text(run_name).click()
        expect(tree.get_by_text("No jobs")).to_be_visible(timeout=60_000)

    def test_the_tree_and_the_table_say_the_same_thing(self) -> None:
        """One status vocabulary: the same job reads the same in both."""
        name = _experiment_name(self)
        self.addCleanup(cortexgrid.delete_experiment, name)
        exp = cortexgrid.Experiment.init(name)
        run_name = exp.run_name()
        job_id = cortexgrid.remote(_noop_job).job_id

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Jobs").click()
        row = self.page.locator(".jobs-dashboard__row", has_text=job_id)
        expect(row).to_be_visible(timeout=60_000)
        in_table = row.locator(".job-status").inner_text()

        self.page.get_by_role("button", name="Experiments").click()
        tree = self.page.locator(".experiment-tree__list")
        tree.get_by_text(name).click()
        tree.get_by_text(run_name).click()
        job_row = tree.locator(".experiment-tree__row", has_text=job_id)
        expect(job_row).to_be_visible(timeout=60_000)

        expect(job_row.locator(f".job-status--{in_table}")).to_be_visible()
