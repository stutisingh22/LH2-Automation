"""
Regression checks for Outreach State Engine (no DB):
transition policy, idempotent status resolution, reply classifier sanity.
"""

from __future__ import annotations

import unittest

from services.outreach_classifier import classify_inbound_reply
from services.outreach_state import (
    canonical_outreach_status,
    resolve_action_status_for_patch,
    should_apply_sync_status,
)


class TestOutreachState(unittest.TestCase):
    def test_canonical_completed_is_sent(self):
        self.assertEqual(canonical_outreach_status("completed"), "sent")

    def test_no_downgrade_sent_to_planned(self):
        self.assertFalse(should_apply_sync_status("sent", "planned"))

    def test_upgrade_planned_to_sent(self):
        self.assertTrue(should_apply_sync_status("planned", "sent"))
        self.assertEqual(resolve_action_status_for_patch("planned", "sent"), "sent")

    def test_reply_once_meeting_wins_negative(self):
        neg = classify_inbound_reply("Re: hi", "Not interested, please stop emailing")
        self.assertEqual(neg.action_status, "replied_negative")
        pos = classify_inbound_reply("Re: hi", "Yes — here's my Calendly link for Thursday")
        self.assertEqual(pos.action_status, "meeting_booked")

    def test_question_is_not_meeting(self):
        res = classify_inbound_reply(
            "Re: Acquiring A Spicy Perspective",
            "Sure but can you tell me how it aligns with our editorial voice?",
        )
        self.assertNotEqual(res.action_status, "meeting_booked")
        self.assertEqual(res.action_status, "replied_positive")

    def test_time_alone_is_not_meeting(self):
        res = classify_inbound_reply("Re: hi", "On Fri, Mar 27 at 3:08 PM wrote:")
        self.assertNotEqual(res.action_status, "meeting_booked")

    def test_calendar_link_is_meeting(self):
        res = classify_inbound_reply("Re: hi", "Happy to chat. Here is my Calendly link.")
        self.assertEqual(res.action_status, "meeting_booked")

    def test_terminal_deal_lost_blocks_positive_resync(self):
        self.assertFalse(should_apply_sync_status("deal_lost", "replied_positive"))

    def test_classify_unsubscribe_deal_lost(self):
        lost = classify_inbound_reply("Re: hi", "Please unsubscribe me from this list")
        self.assertEqual(lost.action_status, "deal_lost")

    def test_planned_can_receive_reply_in_policy(self):
        self.assertTrue(should_apply_sync_status("planned", "replied_positive"))
        self.assertEqual(
            resolve_action_status_for_patch("planned", "replied_positive"),
            "replied_positive",
        )


if __name__ == "__main__":
    unittest.main()
