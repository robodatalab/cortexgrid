from __future__ import annotations

from playwright.sync_api import expect

from tests.integration.cortexflow_ui._base import UI_URL, UITestCase


class TestInfraStatus(UITestCase):
    def test_indicator_state_matches_overall_api(self) -> None:
        self.page.goto(UI_URL)
        api = self.page.request.get(f"{UI_URL}/api/infra/status").json()
        expected_label = "Infra: OK" if api["overall"] else "Infra: Error"
        expect(self.page.get_by_label(expected_label)).to_be_visible(timeout=30_000)

    def test_dashboard_pod_states_match_api(self) -> None:
        self.page.goto(UI_URL)
        expect(self.page.get_by_label("Infra: OK")).to_be_visible(timeout=30_000)
        self.page.get_by_label("Infra: OK").click()

        dashboard = self.page.locator(".infra-dashboard")
        dashboard.locator(".titled-frame").first.wait_for(state="visible", timeout=10_000)

        api = self.page.request.get(f"{UI_URL}/api/infra/status").json()
        expected = {p["name"]: p["healthy"] for p in api["pods"]}

        cards = dashboard.locator(".titled-frame")
        actual: dict[str, bool] = {}
        for i in range(cards.count()):
            card = cards.nth(i)
            name = card.locator(".titled-frame__title").inner_text()
            heart_class = card.locator(".infra-card__heart").get_attribute("class") or ""
            actual[name] = "infra-card__heart--ok" in heart_class

        self.assertEqual(expected, actual)
