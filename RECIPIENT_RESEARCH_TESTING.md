# ✅ Recipient Research - Testing Checklist

Use this checklist to verify the feature is working correctly.

---

## Pre-Testing Setup

- [ ] Ensure `ANTHROPIC_API_KEY` is set in `keys.env`
- [ ] Database is accessible (PostgreSQL or SQLite)
- [ ] App starts without errors: `streamlit run apps.py`
- [ ] Can navigate to Strategist tab

---

## Basic Functionality Tests

### Test 1: Auto-Load Research
- [ ] Go to Strategist tab
- [ ] Select a company with contacts
- [ ] Select a contact that has an email address
- [ ] See spinner: "Researching recipient..." (8-12 seconds)
- [ ] Brief section appears: "📊 Recipient Brief: [Name] · Updated [time]"
- [ ] No errors in console/logs

**Expected:** Research loads automatically, brief displays

### Test 2: View Research Details
- [ ] Click to expand the brief section
- [ ] See "Quick Context" summary (2-3 sentences)
- [ ] See "Recent LinkedIn Focus" section (if available)
- [ ] See "Twitter" section (if available)
- [ ] See "Recent Content" section (if available)
- [ ] All text renders correctly (no raw JSON, no errors)

**Expected:** Research displays in clean, readable format

### Test 3: Cache Behavior
- [ ] Research a recipient (step 1)
- [ ] Switch to a different contact
- [ ] Switch back to the original contact
- [ ] Brief loads instantly (no 8-12 second wait)
- [ ] Timestamp shows same "Updated X ago"

**Expected:** Cached research loads instantly

### Test 4: Manual Refresh
- [ ] Expand recipient brief section
- [ ] Click "🔄 Refresh Research" button
- [ ] See spinner while refreshing (8-12 seconds)
- [ ] Timestamp updates to "Updated today" or similar
- [ ] Brief content may have changed (if recipient's online activity changed)

**Expected:** Fresh research loads, timestamp updates

---

## Edge Cases

### Test 5: Contact Without Email
- [ ] Select a contact with no email address
- [ ] Brief section does NOT auto-load
- [ ] See message: "💡 Recipient research helps personalize your outreach"
- [ ] See button: "🔍 Research Recipient"
- [ ] Clicking the button shows error or no research (expected - needs email)

**Expected:** Graceful handling of missing email

### Test 6: Multiple Recipients
- [ ] Research recipient A
- [ ] Research recipient B
- [ ] Research recipient C
- [ ] Switch between A, B, C
- [ ] Each shows their own research (not mixed up)
- [ ] Each loads from cache (instant)

**Expected:** Research is per-contact, not shared

### Test 7: Session Persistence
- [ ] Research a recipient
- [ ] Refresh the browser page (F5)
- [ ] Navigate back to Strategist tab
- [ ] Select the same recipient
- [ ] Research loads from database cache (instant)

**Expected:** Research persists across page refreshes

---

## AI Integration Tests

### Test 8: Email Generation
- [ ] Research a recipient (ensure brief shows topics)
- [ ] Click "📧 Email" quick action button
- [ ] AI generates email draft
- [ ] Check if email mentions recipient's interests/topics
- [ ] Check if email references LinkedIn activity or blog posts

**Expected:** AI uses research for personalization

### Test 9: Outreach Sequence
- [ ] Research a recipient
- [ ] Click "🎯 Full Plan" button
- [ ] Wait for sequence generation
- [ ] Expand each step to read content
- [ ] Check if messages reference recipient's interests
- [ ] Verify personalization appears in multiple steps

**Expected:** Sequence is personalized based on research

### Test 10: Chat Interaction
- [ ] Research a recipient
- [ ] Type in chat: "Draft an email mentioning their recent activity"
- [ ] AI should reference specific topics from research
- [ ] Type: "What should I talk about with them?"
- [ ] AI should suggest topics based on research

**Expected:** AI strategist uses research in responses

---

## Error Handling Tests

### Test 11: API Failure
- [ ] Temporarily remove `ANTHROPIC_API_KEY` from environment
- [ ] Restart app
- [ ] Select a recipient
- [ ] Research should fail gracefully (no crash)
- [ ] Strategist tab still works normally
- [ ] Can still use quick actions (without personalization)

**Expected:** Graceful degradation, no crash

### Test 12: Invalid Data
- [ ] Manually edit database to corrupt research data
- [ ] Select the corrupted contact
- [ ] App should handle gracefully
- [ ] Can click refresh to fix

**Expected:** No crash, refresh can recover

---

## Performance Tests

### Test 13: Initial Research Speed
- [ ] Select new recipient (no cache)
- [ ] Time how long research takes
- [ ] Should complete in 8-12 seconds

**Expected:** ✅ <15 seconds for fresh research

### Test 14: Cached Research Speed
- [ ] Select previously researched recipient
- [ ] Time how long display takes
- [ ] Should be <500ms

**Expected:** ✅ <1 second for cached research

### Test 15: Cache Expiration
- [ ] Research a recipient
- [ ] Manually edit database: set `last_updated` to 8 days ago
- [ ] Select the recipient
- [ ] Should trigger fresh research (not use stale cache)

**Expected:** ✅ Auto-refresh after 7 days

---

## UI/UX Tests

### Test 16: UI Layout
- [ ] Brief section doesn't break page layout
- [ ] Expander opens/closes smoothly
- [ ] Text is readable (not too small)
- [ ] Spacing looks good (not cramped)
- [ ] Fits well between enrichment and quick actions

**Expected:** ✅ Clean, professional appearance

### Test 17: Mobile/Narrow Screen
- [ ] Resize browser to narrow width
- [ ] Brief section still displays correctly
- [ ] Text wraps properly
- [ ] Buttons are clickable

**Expected:** ✅ Responsive design

### Test 18: Multiple Contacts Rapid Switching
- [ ] Rapidly switch between 5+ contacts
- [ ] UI should update smoothly
- [ ] No flickering or loading issues
- [ ] Each contact shows correct research

**Expected:** ✅ Smooth UX, no glitches

---

## Data Quality Tests

### Test 19: Research Content Quality
- [ ] Research a well-known professional (e.g., industry leader)
- [ ] Check if topics are accurate/relevant
- [ ] Check if Twitter handle is correct (if found)
- [ ] Check if blog posts are real (if found)

**Expected:** ✅ Accurate, relevant research

### Test 20: Brief Quality
- [ ] Read the "Quick Context" brief
- [ ] Should be 2-3 sentences
- [ ] Should be actionable for outreach
- [ ] Should mention specific interests/focus areas

**Expected:** ✅ High-quality, useful brief

---

## Integration Tests

### Test 21: Works with Sender Profiles
- [ ] Select different sender profiles (FROM dropdown)
- [ ] Research should work regardless of sender
- [ ] AI should use research + sender voice together

**Expected:** ✅ Compatible with sender profiles

### Test 22: Works with Product Selection
- [ ] Select "Accelerate" product
- [ ] Research a recipient
- [ ] Generate email
- [ ] Should mention both recipient's interests AND product
- [ ] Switch to "MediaOS" product
- [ ] Generate email
- [ ] Should update product while keeping personalization

**Expected:** ✅ Compatible with product selection

### Test 23: Works with Company Research
- [ ] Select company with existing research notes
- [ ] Research a recipient
- [ ] Generate email
- [ ] Should reference both company research AND recipient research

**Expected:** ✅ Compatible with company research

---

## Database Tests

### Test 24: Data Persistence
- [ ] Research recipient A
- [ ] Stop the app
- [ ] Restart the app
- [ ] Select recipient A
- [ ] Research loads from database (instant)

**Expected:** ✅ Data persists across app restarts

### Test 25: Database Size
- [ ] Research 10 recipients
- [ ] Check database size increase
- [ ] Should be ~20-50KB total (2-5KB per contact)

**Expected:** ✅ Minimal database footprint

---

## Security/Privacy Tests

### Test 26: Data Privacy
- [ ] Check what data is stored in database
- [ ] Should only have: topics, professional info, public posts
- [ ] Should NOT have: personal addresses, phone numbers, private info

**Expected:** ✅ Only public professional data

### Test 27: API Key Security
- [ ] Check that API keys are not logged
- [ ] Check that research prompts don't leak sensitive data

**Expected:** ✅ No sensitive data in logs

---

## Acceptance Criteria

All tests should pass for feature to be production-ready:

### Critical (Must Pass)
- ✅ Test 1: Auto-load works
- ✅ Test 2: Display is clean
- ✅ Test 3: Cache works
- ✅ Test 8: AI integration works
- ✅ Test 11: Errors don't crash app

### Important (Should Pass)
- ✅ Test 4: Manual refresh works
- ✅ Test 5: No-email handled gracefully
- ✅ Test 9: Sequences are personalized
- ✅ Test 13: Speed is acceptable (<15s)
- ✅ Test 16: UI looks good

### Nice to Have (Can Fix Later)
- ✅ Test 19: Research quality is high
- ✅ Test 20: Brief is useful
- ✅ Test 25: Database is efficient

---

## Bug Reporting Template

If you find a bug, report it with:

```
**Test Number:** Test XX
**Issue:** Brief description
**Steps to Reproduce:**
1. Step 1
2. Step 2
3. Step 3

**Expected:** What should happen
**Actual:** What actually happened
**Screenshots:** (if applicable)
**Console Errors:** (if any)
```

---

## Success Metrics

After testing, the feature should achieve:

- ✅ **Functionality**: All core features work
- ✅ **Performance**: Research completes in <15s, cache <1s
- ✅ **Quality**: Research is accurate and actionable
- ✅ **UX**: UI is clean and doesn't overwhelm
- ✅ **Reliability**: No crashes or data corruption
- ✅ **Integration**: Works with all existing features

---

## Post-Testing

Once all tests pass:

- [ ] Document any known limitations
- [ ] Update user documentation if needed
- [ ] Train users on the feature
- [ ] Monitor performance in production
- [ ] Collect user feedback
- [ ] Plan future improvements

---

**Happy Testing!** 🚀
