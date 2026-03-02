# Sender Voice Implementation - Complete

## Overview
Implemented a sender-specific tone and voice system for AI-generated outreach messages. The system loads training documents from Google Drive and applies sender-specific voice profiles (Upneet, Aman, and dynamic users) when generating LinkedIn messages, emails, and outreach sequences.

## Components Created/Modified

### 1. **New File: `sender_profiles.py`**
- Defines hardcoded profiles for Upneet Grover (Founder) and Aman Taneja (Corp Dev)
- Each profile includes:
  - Name, title, email, phone, LinkedIn
  - Voice type (e.g., "founder_first_person", "corp_dev_third_person")
  - Voice notes (detailed tone guidance)
  - Signature block
- `get_all_sender_profiles()`: Returns hardcoded profiles + active users from database
- `get_sender_profile(email)`: Retrieves specific profile by email
- `format_sender_display_name(profile)`: Formats profile for UI dropdown

### 2. **Modified: `config.py`**
- Added `CONTEXT_FOLDER_ID` import from environment variables
- This folder contains training docs, outreach guidelines, etc.

### 3. **Modified: `drive_sync.py`**
- Added `get_context_folder_files()` function
- Scans ALL files in the Context folder (CONTEXT_FOLDER_ID)
- Loads Google Docs and PDFs
- Returns concatenated text for AI prompt injection
- **Caches results for 12 hours** (in-memory + database cache)

### 4. **Modified: `ai_engine.py`**
**Functions updated to accept `sender_email` parameter:**
- `_build_system_prompt(comp, ct, uid, sid, research_notes, sender_email=None)`
  - When sender_email provided:
    - Fetches sender profile from `sender_profiles.py`
    - Loads training docs from Context folder via `get_context_folder_files()`
    - Injects both into system prompt under "SENDER CONTEXT" and "OUTREACH TRAINING & GUIDELINES"
- `get_strategist_response(... sender_email=None)`
- `get_strategist_response_stream(... sender_email=None)`
- `auto_brief(... sender_email=None)`
- `generate_outreach_plan(... sender_email=None)`
- `replan_on_response(... sender_email=None)`
- `get_advisor_system_prompt(uid, sender_email=None)`
- `advisor_chat(... sender_email=None)`

### 5. **Modified: `pages/strategist.py`**
**UI Changes:**
- Added sender selection dropdown after FROM/TO selectors
- Shows all available senders (hardcoded + database users)
- Displays sender's voice type and source (hardcoded/database)
- Warns if no sender selected
- Stored in `st.session_state.selected_sender` (persists across tabs)

**Backend Changes:**
- Passes `sender_email=st.session_state.selected_sender` to:
  - `get_strategist_response_stream()`

### 6. **Modified: `pages/actions.py`**
**UI Changes:**
- Added sender selection dropdown at top of page
- Same UI pattern as Strategist tab

**Backend Changes:**
- Passes `sender_email=st.session_state.selected_sender` to:
  - `replan_on_response()`

### 7. **Modified: `pages/advisor.py`**
**UI Changes:**
- Added sender selection dropdown after history selector
- Same UI pattern as Strategist/Actions tabs

**Backend Changes:**
- Passes `sender_email=st.session_state.selected_sender` to:
  - `advisor_chat()`

## How It Works

### Flow:
1. **User selects sender** in Strategist, Actions, or Advisor tab
2. Selection stored in `st.session_state.selected_sender` (persists across tabs)
3. **When AI generates outreach:**
   - `_build_system_prompt()` checks if `sender_email` is provided
   - If yes:
     - Fetches sender profile from `sender_profiles.py`
     - Loads training docs from Context folder (cached)
     - Injects both into system prompt
4. **AI receives:**
   - Sender name, title, voice type, voice notes, signature
   - Full training document (LH2 Holdings - Outreach Agent: Training Material & Guidelines)
5. **AI generates message** in sender's voice with appropriate tone

### Caching Strategy:
- **Training docs (Context folder):** Cached for 12 hours (in-memory + database)
- **Sender profiles:** Hardcoded profiles loaded instantly, database users queried live
- **Follows "Active Rule":** External API calls (AI, enrichment) NOT cached

## Files Modified Summary
```
NEW:
- sender_profiles.py

MODIFIED:
- config.py
- drive_sync.py
- ai_engine.py
- pages/strategist.py
- pages/actions.py
- pages/advisor.py
```

## Configuration Required

### 1. Environment Variables (`keys.env`):
```env
CONTEXT_FOLDER_ID=1HkBiRBm_oti9-d6L2FaTeotd8UrRNXup  # Already set
```

### 2. Training Document:
- Upload "LH2 Holdings - Outreach Agent: Training Material & Guidelines.pdf" to the Context folder
- The system will automatically scan and load all files in that folder

## Usage

### For Users:
1. Open Strategist, Actions, or Advisor tab
2. Select sender from dropdown (e.g., "Upneet Grover (Founder & CEO, LH2 Holdings)")
3. Generate outreach (LinkedIn messages, emails, sequences, replans)
4. AI will use selected sender's voice and tone

### For Admins (Adding New Senders):
**Option 1: Add to Database**
- Add user to `users` table with email
- They'll automatically appear in sender dropdown

**Option 2: Add Hardcoded Profile** (for detailed voice control)
- Edit `sender_profiles.py`
- Add new entry to `HARDCODED_PROFILES` dict
- Include name, title, email, voice_type, voice_notes, signature

## Architecture Decisions

### Why Context Folder (not database/code)?
- Training docs can be updated in Drive without code changes
- Non-technical team members can update guidelines
- All files in folder automatically loaded (extensible)

### Why Hardcoded + Database Hybrid?
- **Hardcoded (Upneet, Aman):** Full voice control, detailed tone guidance
- **Database users:** Automatic inclusion, no code changes needed
- **Best of both worlds:** Power users get custom profiles, others get defaults

### Why Session State (not per-tab)?
- User selects once, applies everywhere
- Reduces friction (don't need to re-select per tab)
- Natural UX: "I'm Upneet today" vs "I'm Upneet in Strategist but Aman in Actions"

## Testing Checklist
- [x] Sender profiles load correctly
- [x] Context folder files load and cache
- [x] Strategist tab: sender selection visible, passed to AI
- [x] Actions tab: sender selection visible, passed to AI
- [x] Advisor tab: sender selection visible, passed to AI
- [x] Training docs injected into system prompt
- [x] Sender profile injected into system prompt
- [ ] Test actual outreach generation with different senders (manual testing needed)
- [ ] Verify tone differences in generated messages (manual testing needed)

## Next Steps (User Testing)
1. Upload training PDF to Context folder
2. Test generating LinkedIn message as Upneet vs Aman
3. Verify tone/voice differences match training doc
4. Adjust voice_notes in `sender_profiles.py` if needed
5. Consider adding more senders (Stuti, Ashish) as hardcoded profiles if detailed control needed
