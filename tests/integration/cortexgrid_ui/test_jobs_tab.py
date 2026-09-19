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

    def test_deleted_job_is_gone_from_the_table_and_from_its_run(self) -> None:
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

        # The delete stops the job's Ray attempt and waits for it to settle,
        # so it takes longer than an ordinary request.
        expect(row).not_to_be_visible(timeout=90_000)

        self.page.get_by_role("button", name="Experiments").click()
        tree = self.page.locator(".experiment-tree__list")
        tree.get_by_text(name).click()
        tree.get_by_text(run_name).click()
        expect(tree.get_by_text("No jobs")).to_be_visible(timeout=60_000)
