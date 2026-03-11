"""Pipeline page — contact pipeline by status."""

import streamlit as st

from config import CONTACT_STATUSES, CONTACT_STATUS_LABELS_STRIP, normalize_contact_status
from db_ops import get_all_companies, get_contacts
from ui_helpers import lh2_section_header, lh2_kpi_cell, lh2_pipeline_row


def render():
    st.markdown(lh2_section_header("CONTACT PIPELINE"), unsafe_allow_html=True)
    all_comp = get_all_companies()
    pipeline = {s: [] for s in CONTACT_STATUSES}
    for cd in all_comp:
        for ctd in get_contacts(cd["id"]):
            status = normalize_contact_status(ctd.get("status", "not_contacted"))
            if status in pipeline:
                pipeline[status].append({"contact": ctd, "company": cd})

    kpi_pipeline = [(len(pipeline[s]), CONTACT_STATUS_LABELS_STRIP.get(s, s)) for s in CONTACT_STATUSES]
    st.markdown(
        "<div class='lh2-kpi-strip'>" + "".join(lh2_kpi_cell(n, lbl) for n, lbl in kpi_pipeline) + "</div>",
        unsafe_allow_html=True,
    )
    bcols = st.columns(len(CONTACT_STATUSES))
    for i, s in enumerate(CONTACT_STATUSES):
        with bcols[i]:
            for item in pipeline[s]:
                ctd, cd = item["contact"], item["company"]
                st.markdown(lh2_pipeline_row(ctd["name"], cd["domain"], ""), unsafe_allow_html=True)
            if not pipeline[s]:
                st.markdown(
                    "<div style='text-align:center;padding:24px 0;font-size:11px;color:var(--text-2);'>—</div>",
                    unsafe_allow_html=True,
                )
