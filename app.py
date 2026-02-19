import streamlit as st
import os

# 1. THE PASSWORD (Tell your team: LH2_2026)
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if not st.session_state["password_correct"]:
        pwd = st.text_input("LH2 Team Password", type="password")
        if pwd == "LH2_2026":
            st.session_state["password_correct"] = True
            st.rerun()
        return False
    return True

if not check_password():
    st.stop()

st.title("LH2 Holdings: AI Outreach Control")

# 2. THE CHAT WINDOW
st.subheader("Edit AI Strategy")
user_prompt = st.text_area("What should Claude focus on?", 
                            value="Draft a strategic partnership email. Keep it brief.", 
                            height=200)

if st.button("🚀 Generate Drafts in Gmail"):
    st.info("Bot is working... checking Notion leads now.")
    # This button will eventually trigger your automation logic
    st.success("Drafts created! Tell the team to check the 'Drafts' folder.")