"""
API tests for Publisher CRM "add publisher" endpoints (mocked DB).
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from api.deps import get_current_user
from api.main import app


def _admin():
    return {"id": 1, "username": "admin", "email": "a@test.com", "role": "admin"}


def _strategist():
    return {"id": 2, "username": "strat", "email": "s@test.com", "role": "strategist"}


def _viewer():
    return {"id": 3, "username": "view", "email": "v@test.com", "role": "viewer"}


class TestCreateIndependentPublisher(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_viewer_forbidden(self):
        app.dependency_overrides[get_current_user] = _viewer
        client = TestClient(app)
        r = client.post("/api/companies", json={"domain": "newpub.com"})
        self.assertEqual(r.status_code, 403)

    def test_409_when_domain_exists(self):
        app.dependency_overrides[get_current_user] = _admin
        client = TestClient(app)
        with patch(
            "api.routers.companies.get_company_by_domain",
            return_value={"id": 42, "domain": "dup.com", "extra_data": {}},
        ):
            r = client.post("/api/companies", json={"domain": "dup.com", "name": "Dup"})
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertIn("detail", body)
        self.assertEqual(body["detail"]["company_id"], 42)

    def test_create_ok(self):
        app.dependency_overrides[get_current_user] = _strategist
        client = TestClient(app)
        with patch("api.routers.companies.get_company_by_domain", return_value=None):
            with patch("api.routers.companies.upsert_company", return_value=100):
                with patch("api.routers.companies.upsert_contact", return_value=200):
                    with patch("api.routers.companies.get_company", return_value={"id": 100, "domain": "x.com"}):
                        with patch("api.routers.companies.get_contact", return_value={"id": 200, "company_id": 100}):
                            with patch(
                                "api.routers.companies.compute_lead_score",
                                return_value=5,
                            ):
                                with patch(
                                    "api.routers.companies.get_or_create_session",
                                    return_value=999,
                                ):
                                    r = client.post(
                                        "/api/companies",
                                        json={
                                            "domain": "x.com",
                                            "name": "X Pub",
                                            "contacts": [
                                                {"name": "Jane", "email": "j@x.com", "role": "Editor"},
                                            ],
                                        },
                                    )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("company_id"), 100)
        self.assertEqual(data.get("session_id"), 999)


class TestCreateMediaBrand(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_mixed_domain_forbidden(self):
        app.dependency_overrides[get_current_user] = _admin
        client = TestClient(app)
        r = client.post("/api/advisor/companies", json={"domain": "__bad.com", "parent_name": "P"})
        self.assertEqual(r.status_code, 400)

    def test_viewer_forbidden(self):
        app.dependency_overrides[get_current_user] = _viewer
        client = TestClient(app)
        r = client.post("/api/advisor/companies", json={"domain": "brand.com", "parent_name": "Acme"})
        self.assertEqual(r.status_code, 403)

    def test_409_when_exists(self):
        app.dependency_overrides[get_current_user] = _admin
        client = TestClient(app)
        with patch(
            "api.routers.advisor.get_advisor_company_by_domain_and_parent",
            return_value={"id": 7, "domain": "b.com", "parent_name": "Acme"},
        ):
            r = client.post(
                "/api/advisor/companies",
                json={"domain": "b.com", "parent_name": "Acme"},
            )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["advisor_company_id"], 7)

    def test_create_returns_ids(self):
        app.dependency_overrides[get_current_user] = _admin
        client = TestClient(app)
        with patch("api.routers.advisor.get_advisor_company_by_domain_and_parent", return_value=None):
            with patch("api.routers.advisor.upsert_advisor_company", return_value=55):
                with patch("api.routers.advisor.merge_advisor_company_extra_data", return_value=True):
                    with patch(
                        "api.routers.advisor.get_or_create_shadow_company_for_advisor",
                        return_value=(501, 502),
                    ):
                        with patch("api.routers.advisor.update_contact"):
                            with patch("api.routers.advisor.get_company", return_value={"id": 501}):
                                with patch("api.routers.advisor.get_contact", return_value={"id": 502}):
                                    with patch(
                                        "api.routers.advisor.compute_lead_score",
                                        return_value=1,
                                    ):
                                        with patch(
                                            "api.routers.advisor.get_or_create_session",
                                            return_value=888,
                                        ):
                                            r = client.post(
                                                "/api/advisor/companies",
                                                json={
                                                    "domain": "child.com",
                                                    "parent_name": "Big Media",
                                                    "leadership_email": "lead@child.com",
                                                },
                                            )
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d.get("ok"))
        self.assertEqual(d["advisor_company_id"], 55)
        self.assertEqual(d["company_id"], 501)
        self.assertEqual(d["session_id"], 888)
        self.assertEqual(d["parent_key"], "parent:Big Media")
        self.assertEqual(d.get("contact_ids"), [502])

    def test_create_multiple_spocs_calls_upsert(self):
        app.dependency_overrides[get_current_user] = _admin
        client = TestClient(app)
        mock_upsert = Mock(return_value=602)
        with patch("api.routers.advisor.get_advisor_company_by_domain_and_parent", return_value=None):
            with patch("api.routers.advisor.upsert_advisor_company", return_value=55):
                with patch("api.routers.advisor.merge_advisor_company_extra_data", return_value=True):
                    with patch(
                        "api.routers.advisor.get_or_create_shadow_company_for_advisor",
                        return_value=(501, 502),
                    ):
                        with patch("api.routers.advisor.update_contact"):
                            with patch("api.routers.advisor.upsert_contact", mock_upsert):
                                with patch("api.routers.advisor.get_company", return_value={"id": 501}):
                                    with patch("api.routers.advisor.get_contact", return_value={"id": 502}):
                                        with patch(
                                            "api.routers.advisor.compute_lead_score",
                                            return_value=1,
                                        ):
                                            with patch(
                                                "api.routers.advisor.get_or_create_session",
                                                return_value=888,
                                            ):
                                                r = client.post(
                                                    "/api/advisor/companies",
                                                    json={
                                                        "domain": "child.com",
                                                        "parent_name": "Big Media",
                                                        "contacts": [
                                                            {"name": "Lead", "email": "l@child.com"},
                                                            {"name": "Sue", "email": "sue@child.com", "role": "AD"},
                                                        ],
                                                    },
                                                )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mock_upsert.call_count, 1)
        self.assertEqual(mock_upsert.call_args[0][0], 501)
        self.assertEqual(mock_upsert.call_args[0][1], "Sue")
        d = r.json()
        self.assertEqual(d["contact_ids"], [502, 602])


if __name__ == "__main__":
    unittest.main()
