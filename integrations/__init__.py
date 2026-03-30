"""
integrations — Single entry points for all third-party integrations used by the Streamlit app.

Import from here or from integrations.notion, integrations.gmail, etc.
Do not import notion_sync, gmail_service, drive_sync, etc. directly from app code.
"""

# Notion
from integrations.notion import (
    sync_notion_to_db,
    lookup_lead_in_notion,
    fetch_notes_for_page,
    create_notion_contact_page,
    archive_notion_page,
    sync_notion_advisor_to_db,
    create_notion_advisor_page,
    archive_notion_advisor_page,
)

# Gmail + Google OAuth (Gmail)
from integrations.gmail import create_gmail_draft, parse_email_content, get_google_credentials

# Drive
from integrations.drive import (
    list_drive_files,
    create_doc_in_drive,
    refresh_all_drive_contexts,
    get_lh2_knowledge_context,
    build_drive_context,
    get_context_folder_files,
)

# SignalHire (alias used by app)
from integrations.signalhire import get_credits as get_signalhire_credits
from integrations.signalhire import request_enrichment, has_verified_email

# Analytics + tracker
from integrations.analytics import (
    build_platform_analytics_for_domain,
    load_domain_to_platforms,
    get_presence_for_domain,
    load_domain_to_msn_follower_count,
    refresh_syndication_for_domain,
    apply_syndication_refresh_to_company,
    apply_syndication_refresh_to_advisor_company,
)

# Search
from integrations.search import (
    extract_spoc_table_contacts,
    run_linkedin_lookup_for_contacts,
    get_linkedin_url_for_contact,
    verify_parent_from_web,
)

# AI (Claude, research, recipient research)
from integrations.ai import (
    get_local_time,
    get_strategist_response_stream,
    get_strategist_chat_response_stream,
    auto_brief,
    generate_outreach_plan,
    replan_on_response,
    recommend_offering_for_company,
    maybe_update_summary,
    extract_episodic_memories,
    generate_insight_from_outcome,
    parse_ai_actions,
    export_session_csv,
    intelligence_request,
    research_person_for_company,
    summarize_research_to_context,
    advisor_chat,
    refine_email_draft,
    rewrite_sequence_steps_with_research,
    learn_user_preference,
    summarize_advisor_title,
    fetch_research_from_notion,
    run_research,
    research_command_ai,
    split_research_into_sections,
    get_next_section_number,
    format_appended_section,
    classify_domains_independent_or_media_house,
    classify_entries_independent_or_media_house,
    research_recipient,
    format_time_ago,
)
