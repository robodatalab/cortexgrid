from __future__ import annotations

import uuid

import cortexflow
from playwright.sync_api import Page, expect

from tests.integration.cortexflow_ui._base import UI_URL, UITestCase


def _experiment_name() -> str:
    return f"it-{uuid.uuid4().hex[:8]}"


def _open_run(page: Page, experiment: str, run: str) -> None:
    page.goto(UI_URL)
    tree = page.locator(".experiment-tree__list")
    tree.get_by_text(experiment).click()
    tree.get_by_text(run).click()


def _open_experiment(page: Page, experiment: str) -> None:
    page.goto(UI_URL)
    page.locator(".experiment-tree__list").get_by_text(experiment).click()


class TestRunNotes(UITestCase):
    def test_run_note_add_edit_delete_cycle(self) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()
        _open_run(self.page, name, run_name)
        panel = self.page.locator(".run-notes")

        expect(panel.get_by_text("first draft")).not_to_be_visible()
        expect(panel.get_by_text("No notes yet")).to_be_visible()

        panel.get_by_placeholder("Add a note...").fill("first draft")
        panel.get_by_role("button", name="Add").click()
        expect(panel.get_by_text("first draft")).to_be_visible(timeout=10_000)

        panel.get_by_role("button", name="Edit").click()
        panel.get_by_label("Edit note").fill("revised draft")
        panel.get_by_role("button", name="Save").click()
        expect(panel.get_by_text("revised draft")).to_be_visible(timeout=10_000)
        expect(panel.get_by_text("first draft")).not_to_be_visible()

        panel.get_by_role("button", name="Delete").click()
        expect(panel.get_by_text("revised draft")).not_to_be_visible(timeout=10_000)
        expect(panel.get_by_text("No notes yet")).to_be_visible()


class TestExperimentNotes(UITestCase):
    def test_experiment_note_add_edit_delete_cycle(self) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        cortexflow.Experiment.init(name)
        _open_experiment(self.page, name)
        panel = self.page.locator(".experiment-dashboard")

        expect(panel.get_by_text("an experiment note")).not_to_be_visible()
        expect(panel.get_by_text("No notes yet")).to_be_visible()

        panel.get_by_placeholder("Add an experiment note...").fill("an experiment note")
        panel.get_by_role("button", name="Add").click()
        expect(panel.get_by_text("an experiment note")).to_be_visible(timeout=10_000)

        panel.get_by_role("button", name="Edit").click()
        panel.get_by_label("Edit note").fill("amended experiment note")
        panel.get_by_role("button", name="Save").click()
        expect(panel.get_by_text("amended experiment note")).to_be_visible(timeout=10_000)
        expect(panel.get_by_text("an experiment note")).not_to_be_visible()

        panel.get_by_role("button", name="Delete").click()
        expect(panel.get_by_text("amended experiment note")).not_to_be_visible(timeout=10_000)


class TestNotesCrossContext(UITestCase):
    def test_run_note_appears_and_disappears_in_experiment_dashboard(self) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()

        _open_run(self.page, name, run_name)
        run_panel = self.page.locator(".run-notes")
        run_panel.get_by_placeholder("Add a note...").fill("ran-from-run-view")
        run_panel.get_by_role("button", name="Add").click()
        expect(run_panel.get_by_text("ran-from-run-view")).to_be_visible(timeout=10_000)

        viewer = self.new_user()
        _open_experiment(viewer, name)
        viewer_panel = viewer.locator(".experiment-dashboard")
        expect(viewer_panel.get_by_text("ran-from-run-view")).to_be_visible(timeout=10_000)
        expect(viewer_panel.get_by_text(f"Run: {run_name}")).to_be_visible()

        run_panel.get_by_role("button", name="Delete").click()
        expect(viewer_panel.get_by_text("ran-from-run-view")).not_to_be_visible(timeout=10_000)


class TestNotesMultiUser(UITestCase):
    def test_note_added_by_one_user_appears_for_another_viewer(self) -> None:
        name = _experiment_name()
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()

        author = self.page
        viewer = self.new_user()
        _open_run(author, name, run_name)
        _open_run(viewer, name, run_name)

        viewer_panel = viewer.locator(".run-notes")
        expect(viewer_panel.get_by_text("hello viewer")).not_to_be_visible()

        author_panel = author.locator(".run-notes")
        author_panel.get_by_placeholder("Add a note...").fill("hello viewer")
        author_panel.get_by_role("button", name="Add").click()

        expect(viewer_panel.get_by_text("hello viewer")).to_be_visible(timeout=10_000)
