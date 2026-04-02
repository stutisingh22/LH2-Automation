from __future__ import annotations

import unittest
from unittest.mock import patch

from services import bulk_domain_import as bdi


class TestResolveImportJudgment(unittest.TestCase):
    def test_db_existing_overrides_ai(self):
        out = bdi.resolve_import_judgment(
            classified_row={"classification": "independent"},
            exists_db=True,
            existing_advisor={"parent_name": "Acme Media"},
            existing_parent="Acme Media",
            existing_parents=["Acme Media"],
            verify_result=None,
        )
        self.assertEqual(out["classification"], "media_house")
        self.assertEqual(out["parent_confirmed"], "Acme Media")
        self.assertFalse(out["requires_review"])

    def test_ambiguous_parent_match_requires_review(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "media_house",
                "parent_name": "Vox Media Group",
                "parent_match": None,
                "ownership_confidence": 0.62,
                "needs_review": False,
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result=None,
        )
        self.assertEqual(out["classification"], "media_house")
        self.assertTrue(out["requires_review"])
        self.assertEqual(out["match_method"], "contains_ambiguous")

    def test_low_confidence_independent_requires_review(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "independent",
                "ownership_confidence": 0.3,
                "needs_review": False,
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=[],
            verify_result=None,
        )
        self.assertEqual(out["classification"], "independent")
        self.assertTrue(out["requires_review"])

    def test_web_verified_low_confidence_requires_review(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "media_house",
                "parent_name": "Vox Media Group",
                "parent_match": None,
                "ownership_confidence": 0.62,
                "needs_review": False,
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result={
                "parent_match": "Vox Media",
                "confidence": 0.5,
                "match_reason": "exact",
            },
        )
        self.assertEqual(out["classification"], "media_house")
        self.assertTrue(out["requires_review"])
        self.assertEqual(out["decision_reason"], "web_verified_low_confidence_parent_match")

    def test_web_verified_high_confidence_auto_accept(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "media_house",
                "parent_name": "Vox Media Group",
                "parent_match": None,
                "ownership_confidence": 0.62,
                "needs_review": False,
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result={
                "parent_match": "Vox Media",
                "confidence": 0.9,
                "match_reason": "exact",
            },
        )
        self.assertEqual(out["classification"], "media_house")
        self.assertFalse(out["requires_review"])
        self.assertEqual(out["decision_reason"], "web_verified_parent_match")

    def test_web_parent_low_confidence_requires_review(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "independent",
                "parent_name": None,
                "parent_match": None,
                "ownership_confidence": 0.6,
                "needs_review": False,
                "ownership_rationale": "",
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result={
                "parent_match": "Vox Media",
                "parent_name": "Vox Media",
                "confidence": 0.4,
                "direct_parent": "Vox Media",
                "ultimate_parent": "Vox Media",
            },
        )
        self.assertEqual(out["classification"], "media_house")
        self.assertTrue(out["requires_review"])
        self.assertEqual(out["decision_reason"], "web_verified_low_confidence_parent_match")

    def test_web_no_parent_detected_flips_to_independent(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "media_house",
                "parent_name": "Vox Media",
                "parent_match": None,
                "ownership_confidence": 0.8,
                "needs_review": False,
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result={
                "parent_match": None,
                "confidence": 0.1,
                "direct_parent": None,
                "ultimate_parent": None,
                "parent_name": None,
            },
        )
        self.assertEqual(out["classification"], "independent")
        self.assertTrue(out["requires_review"])
        self.assertEqual(out["decision_reason"], "web_no_parent_detected")

    def test_web_explicit_independent_flips_to_independent(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "media_house",
                "parent_name": "Vox Media",
                "parent_match": "Vox Media",
                "ownership_confidence": 0.6,
                "needs_review": False,
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result={
                "explicit_independent": True,
                "explicit_independent_confidence": 0.92,
                "parent_match": None,
                "confidence": 0.1,
                "direct_parent": None,
                "ultimate_parent": None,
            },
        )
        self.assertEqual(out["classification"], "independent")
        self.assertFalse(out["requires_review"])
        self.assertEqual(out["decision_reason"], "web_explicit_independent")

    def test_web_parent_detected_flips_independent_to_media_house(self):
        out = bdi.resolve_import_judgment(
            classified_row={
                "classification": "independent",
                "parent_name": None,
                "parent_match": None,
                "ownership_confidence": 0.9,
                "needs_review": False,
                "ownership_rationale": "",
            },
            exists_db=False,
            existing_advisor=None,
            existing_parent="",
            existing_parents=["Vox Media"],
            verify_result={
                "parent_match": "Vox Media",
                "parent_name": "Vox Media",
                "confidence": 0.95,
                "direct_parent": "Vox Media",
                "ultimate_parent": "Vox Media",
            },
        )
        self.assertEqual(out["classification"], "media_house")
        self.assertFalse(out["requires_review"])
        self.assertEqual(out["parent_confirmed"], "Vox Media")
        self.assertEqual(out["decision_reason"], "web_verified_parent_match")


class TestCommitReviewGating(unittest.TestCase):
    @patch("services.bulk_domain_import.invalidate_group")
    @patch("services.bulk_domain_import.clear_ui_cache")
    @patch("services.bulk_domain_import.get_campaign", return_value={"id": 1})
    @patch("services.bulk_domain_import.get_existing_parent_media_houses", return_value=["Acme Media"])
    @patch("services.bulk_domain_import.get_company_by_domain", return_value=None)
    @patch("services.bulk_domain_import.get_advisor_company_by_domain", return_value=None)
    def test_blocks_requires_review_media_house(
        self,
        _adv_lookup,
        _comp_lookup,
        _parents,
        _campaign,
        _clear_cache,
        _invalidate,
    ):
        rows = [
            {
                "do_import": True,
                "input": "Example",
                "domain": "example.com",
                "classification": "media_house",
                "parent_confirmed": "Acme Media",
                "requires_review": True,
                "decision_reason": "ambiguous_parent_match",
                "decision_confidence": 0.44,
            }
        ]
        out = bdi.commit_rows(rows, allow_new_parents=False, campaign_id=1)
        self.assertEqual(out["added_media_house"], [])
        self.assertEqual(len(out["flagged_for_review"]), 1)
        self.assertEqual(len(out["blocked_for_review_details"]), 1)


if __name__ == "__main__":
    unittest.main()
