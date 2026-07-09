from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import cortexflow
from playwright.sync_api import expect

from tests.integration.stubs.serving import AddConstantServeApp, write_weights
from tests.integration.cortexflow_ui._base import get_test_ui_url, UITestCase


def _experiment_name(test: UITestCase) -> str:
    return f"it-{test._testMethodName}-{uuid.uuid4().hex[:8]}"


def _save_stub_model(suffix: str, family: str, constant: int = 15) -> None:
    """Register a model so it shows up in the UI. These tests exercise the UI
    tree, not inference, so the weights content is irrelevant."""
    with tempfile.TemporaryDirectory() as d:
        write_weights(Path(d), constant)
        cortexflow.save_model(
            Path(d), AddConstantServeApp, suffix=suffix, family=family
        )


class TestModels(UITestCase):
    def test_model_appears_then_disappears_after_delete(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()
        _save_stub_model(suffix="instruct", family="ft-fake")

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Models").click()
        tree = self.page.locator(".models-tree__list")

        expect(tree.get_by_text("ft-fake")).to_be_visible(timeout=30_000)
        leaf = tree.locator(
            ".models-tree__row", has_text=f"instruct · {run_name}"
        )
        expect(leaf).to_be_visible(timeout=30_000)

        leaf.hover()
        leaf.get_by_role(
            "button", name=f"Delete model instruct {run_name}"
        ).click()
        self.page.get_by_role("button", name="Delete", exact=True).click()

        expect(tree.get_by_text(f"instruct · {run_name}")).not_to_be_visible(
            timeout=30_000
        )

    def test_family_delete_removes_every_version_in_family(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)

        exp_a = cortexflow.Experiment.init(name)
        run_a = exp_a.run_name()
        _save_stub_model(suffix="instruct", family="ft-fake")
        cortexflow.Experiment.close()

        exp_b = cortexflow.Experiment.init(name)
        run_b = exp_b.run_name()
        _save_stub_model(suffix="chat", family="ft-fake", constant=20)

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Models").click()
        tree = self.page.locator(".models-tree__list")

        expect(tree.get_by_text(f"instruct · {run_a}")).to_be_visible(
            timeout=30_000
        )
        expect(tree.get_by_text(f"chat · {run_b}")).to_be_visible(timeout=30_000)

        family_row = tree.locator(".models-tree__row", has_text="ft-fake")
        family_row.hover()
        family_row.get_by_role("button", name="Delete family ft-fake").click()
        self.page.get_by_role("button", name="Delete", exact=True).click()

        expect(tree.get_by_text(f"instruct · {run_a}")).not_to_be_visible(
            timeout=30_000
        )
        expect(tree.get_by_text(f"chat · {run_b}")).not_to_be_visible(
            timeout=30_000
        )

    def test_model_card_run_link_navigates_to_run(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()
        _save_stub_model(suffix="instruct", family="ft-fake")

        self.page.goto(get_test_ui_url())
        self.page.get_by_role("button", name="Models").click()
        tree = self.page.locator(".models-tree__list")

        expect(tree.get_by_text(f"instruct · {run_name}")).to_be_visible(
            timeout=30_000
        )
        tree.get_by_text(f"instruct · {run_name}").click()

        card = self.page.locator(".model-dashboard")
        expect(card.locator(".model-dashboard__title")).to_have_text(
            "ft-fake / instruct"
        )
        card.locator(".model-dashboard__link").click()

        run_title = self.page.locator(".run-dashboard__title")
        expect(run_title).to_have_text(f"{name} / {run_name}", timeout=30_000)

    def test_run_dashboard_models_section_navigates_to_model_card(self) -> None:
        name = _experiment_name(self)
        self.addCleanup(cortexflow.delete_experiment, name)
        exp = cortexflow.Experiment.init(name)
        run_name = exp.run_name()
        _save_stub_model(suffix="instruct", family="ft-fake")

        self.page.goto(get_test_ui_url())
        tree = self.page.locator(".experiment-tree__list")
        expect(tree.get_by_text(name)).to_be_visible(timeout=30_000)
        tree.get_by_text(name).click()
        expect(tree.get_by_text(run_name)).to_be_visible(timeout=30_000)
        tree.get_by_text(run_name).click()

        run_models = self.page.locator(".run-dashboard__model-list")
        expect(run_models.get_by_text("ft-fake / instruct")).to_be_visible(
            timeout=30_000
        )
        run_models.get_by_text("ft-fake / instruct").click()

        card = self.page.locator(".model-dashboard__title")
        expect(card).to_have_text("ft-fake / instruct", timeout=30_000)
