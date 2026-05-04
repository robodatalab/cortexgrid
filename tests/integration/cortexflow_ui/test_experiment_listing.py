from __future__ import annotations

import uuid

import cortexflow

from tests.integration.cortexflow_ui._base import UI_URL, UITestCase


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


def _noop_job() -> None:
    cortexflow.log_metric("ran", 1.0)


class TestExperimentListing(UITestCase):
    def test_new_experiment_appears_while_ui_is_open(self) -> None:
        self.page.goto(UI_URL)
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        self.page.get_by_text(name).wait_for(state="visible", timeout=30_000)

    def test_new_run_and_job_appear_under_experiment(self) -> None:
        self.page.goto(UI_URL)
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()
        job_id = cortexflow.remote(_noop_job)

        self.page.get_by_text(name).click()
        self.page.get_by_text(run_name).wait_for(state="visible", timeout=30_000)
        self.page.get_by_text(run_name).click()
        self.page.get_by_text(job_id).wait_for(state="visible", timeout=30_000)
