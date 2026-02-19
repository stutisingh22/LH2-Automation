"""
ui_helpers.py — CSS injection, badges, cards, formatting helpers

OVERHAUL:
  - ChatGPT-style chat bubbles
  - Better message styling with avatars
  - Cleaner sidebar for conversation history
  - Improved overall polish
"""

import streamlit as st
from config import ROLE_PERMISSIONS


# ── CSS ───────────────────────────────────────────────────────

def inject_css():
    st.markdown("""
<style>
/* v3.0 - Text navigation with underline */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ── Base ── */
:root{
  --bg:#f7f8fa;--surface:#fff;--border:#e2e5ea;--border-light:#eef0f3;
  --text:#1a1d21;--text-2:#4a5568;--text-3:#8b95a2;
  --accent:#2563eb;--accent-bg:#eff6ff;--accent-dark:#1d4ed8;
  --success:#16a34a;--success-bg:#dcfce7;
  --warn:#b45309;--warn-bg:#fef3c7;
  --danger:#dc2626;--danger-bg:#fee2e2;
  --radius:10px;
  --chat-user:#eff6ff;--chat-ai:#fff;
}
.stApp{background:var(--bg);color:var(--text);font-family:'Inter',-apple-system,system-ui,sans-serif}
#MainMenu,footer,header{visibility:hidden}
.block-container{padding:0.5rem 2rem 2rem;max-width:1400px}
p,li,span,div,label,.stMarkdown,td,th{color:var(--text)!important;font-size:15px!important;line-height:1.65!important}

/* Force sidebar to always be visible and prevent collapse */
[data-testid="collapsedControl"]{display:none!important;visibility:hidden!important;pointer-events:none!important}
button[kind="header"]{display:none!important}
section[data-testid="stSidebar"]{min-width:21rem!important;max-width:21rem!important;transform:none!important}
section[data-testid="stSidebar"] > div{width:21rem!important}
section[data-testid="stSidebar"][aria-expanded="false"]{transform:translateX(0)!important}
[data-testid="stSidebarNav"]{display:block!important}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"]{gap:0;background:var(--surface);border-bottom:1px solid var(--border);padding:0 1rem;border-radius:var(--radius) var(--radius) 0 0}
.stTabs [data-baseweb="tab"]{height:44px;font-size:11px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--text-3)!important;background:transparent!important;border:none!important;border-bottom:2px solid transparent!important;padding:0 1rem;transition:all .12s}
.stTabs [aria-selected="true"]{color:var(--text)!important;border-bottom-color:var(--accent)!important}
.stTabs [data-baseweb="tab"]:hover{color:var(--text-2)!important}

/* ── Clean Admin Dashboard Header ── */
.dashboard-header-left{
  display:flex;
  align-items:center;
  padding:8px 0 0 0;
}
.logo-container{
  display:flex;
  align-items:center;
  gap:10px;
}
.logo-diamond{
  font-size:18px;
  color:var(--accent);
  filter:drop-shadow(0 2px 4px rgba(37,99,235,.2));
}
.logo-text{
  font-size:12px;
  font-weight:800;
  letter-spacing:1.5px;
  background:linear-gradient(135deg, var(--accent), var(--accent-dark));
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
  background-clip:text;
}
.logo-subtitle{
  font-size:10px;
  font-weight:600;
  letter-spacing:0.5px;
  color:var(--text-3);
  margin-left:10px;
  padding-left:10px;
  border-left:2px solid var(--border-light);
}
.breadcrumb-container{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:8px;
  padding:6px 16px;
  background:linear-gradient(135deg, #f0f7ff, #e8f2ff);
  border-radius:16px;
  border:1px solid #c7d2fe;
}
.breadcrumb-item{
  font-size:12px;
  font-weight:600;
  color:var(--accent-dark);
}
.breadcrumb-sep{
  color:var(--accent);
  opacity:0.5;
}
.dashboard-header-right{
  display:flex;
  justify-content:flex-end;
  align-items:center;
  padding:2px 0 0 0;
  min-height:28px;
}
.user-badge{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:3px 8px 3px 3px;
  background:var(--surface);
  border:1px solid var(--border-light);
  border-radius:999px;
  transition:all .2s ease;
  box-shadow:0 1px 2px rgba(0,0,0,.04);
}
.user-badge:hover{
  border-color:var(--border);
  box-shadow:0 2px 6px rgba(0,0,0,.06);
}
.user-avatar{
  width:20px;
  height:20px;
  border-radius:50%;
  background:linear-gradient(145deg, var(--accent), var(--accent-dark));
  color:#fff;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:9px;
  font-weight:600;
  flex-shrink:0;
  letter-spacing:0;
}
.user-info{
  display:flex;
  flex-direction:column;
  align-items:flex-start;
  gap:0;
  line-height:1.15;
}
.user-badge .user-name{
  font-size:9px!important;
  font-weight:600!important;
  color:var(--text)!important;
  letter-spacing:0.02em!important;
  line-height:1.15!important;
}
.user-badge .user-role{
  font-size:7px!important;
  font-weight:500!important;
  color:var(--text-3)!important;
  text-transform:uppercase!important;
  letter-spacing:0.5px!important;
  line-height:1.15!important;
}

/* ── Navigation Tabs (st.tabs styling) ── */
/* Tabs container — match spacing above nav to spacing inside nav (tab padding) */
.stTabs [data-baseweb="tab-list"]{
  gap:32px;
  border-bottom:1px solid var(--border);
  padding-bottom:0;
  margin-top:12px;
  margin-bottom:24px;
}
/* Individual tab buttons */
.stTabs [data-baseweb="tab"]{
  background:transparent!important;
  border:none!important;
  border-bottom:2px solid transparent!important;
  border-radius:0!important;
  color:var(--text-2)!important;
  font-size:13px!important;
  font-weight:500!important;
  letter-spacing:0.5px!important;
  text-transform:uppercase!important;
  padding:12px 4px!important;
  margin-bottom:-1px!important;
  height:auto!important;
  white-space:nowrap!important;
}
.stTabs [data-baseweb="tab"]:hover{
  background:transparent!important;
  color:var(--accent)!important;
}
/* Active tab */
.stTabs [data-baseweb="tab"][aria-selected="true"]{
  background:transparent!important;
  color:var(--accent)!important;
  border-bottom:2px solid var(--accent)!important;
  font-weight:600!important;
}
/* Remove default focus ring */
.stTabs [data-baseweb="tab"]:focus{
  outline:none!important;
}
/* Tab panels */
.stTabs [data-baseweb="tab-panel"]{
  padding-top:0!important;
}

/* ── Inputs ── */
.stTextInput>div>div>input,.stTextArea>div>div>textarea{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--text)!important;border-radius:8px!important;font-size:15px!important;padding:10px 14px!important}
.stTextInput>div>div>input:focus,.stTextArea>div>div>textarea:focus{border-color:var(--accent)!important;box-shadow:0 0 0 3px rgba(37,99,235,.06)!important}
.stSelectbox>div>div{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:8px!important}
.stSelectbox label,.stTextInput label,.stTextArea label{color:var(--text-2)!important;font-weight:500!important;font-size:12px!important;letter-spacing:.3px}

/* ── Sidebar: Compact Admin Panel Cards with Icon ── */
/* Session card wrapper with overlay button */
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)){
  position:relative!important;
  margin-bottom:8px!important;
}
/* Make the right column invisible but keep it clickable */
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)) > div:nth-child(2){
  position:absolute!important;
  top:0!important;
  left:0!important;
  right:0!important;
  bottom:0!important;
  width:100%!important;
  height:100%!important;
  z-index:5!important;
}
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)) > div:nth-child(2) .stButton{
  width:100%!important;
  height:100%!important;
}
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)) > div:nth-child(2) .stButton>button{
  width:100%!important;
  height:100%!important;
  background:transparent!important;
  border:none!important;
  opacity:0!important;
  cursor:pointer!important;
  padding:0!important;
}
/* Compact admin panel session cards */
.sidebar-card{
  background:var(--surface)!important;
  border:1px solid var(--border)!important;
  border-radius:8px!important;
  padding:10px!important;
  margin-bottom:8px!important;
  transition:all .2s ease!important;
  position:relative!important;
}
.sidebar-card:hover{
  background:var(--surface)!important;
  border-color:var(--accent)!important;
  box-shadow:0 2px 8px rgba(37,99,235,.12)!important;
  transform:translateY(-1px)!important;
}
/* Full width arrow button for session cards */
[data-testid="stSidebar"] .stButton{
  width:100%!important;
}
[data-testid="stSidebar"] .stButton > button[kind="secondary"]{
  width:100%!important;
  height:32px!important;
  min-height:32px!important;
  padding:6px!important;
  margin:0 0 12px 0!important;
  background:rgba(37,99,235,.08)!important;
  border:1px solid rgba(37,99,235,.2)!important;
  border-radius:6px!important;
  color:var(--accent)!important;
  font-size:14px!important;
  font-weight:600!important;
  transition:all .2s ease!important;
  text-align:center!important;
}
[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover{
  background:rgba(37,99,235,.15)!important;
  border-color:var(--accent)!important;
  transform:translateY(-1px)!important;
  box-shadow:0 2px 6px rgba(37,99,235,.15)!important;
}
.sidebar-card.active{
  background:rgba(37,99,235,.06)!important;
  border-color:var(--accent)!important;
  box-shadow:0 0 0 1px rgba(37,99,235,.2)!important;
}
.card-header{
  display:flex!important;
  justify-content:space-between!important;
  align-items:flex-start!important;
  margin-bottom:6px!important;
  gap:6px!important;
}
.card-domain{
  font-size:14px!important;
  font-weight:700!important;
  color:var(--text)!important;
  line-height:1.2!important;
  flex:1!important;
}
.card-badges{
  display:flex!important;
  gap:4px!important;
  flex-wrap:wrap!important;
  justify-content:flex-end!important;
}
.session-badge{
  display:inline-block!important;
  padding:2px 6px!important;
  font-size:9px!important;
  font-weight:700!important;
  letter-spacing:0.5px!important;
  border-radius:4px!important;
  line-height:1!important;
}
.badge-new{
  background:#dcfce7!important;
  color:#166534!important;
}
.badge-active{
  background:#dbeafe!important;
  color:#1e40af!important;
}
.badge-ongoing{
  background:#fef3c7!important;
  color:#92400e!important;
}
.badge-memory{
  background:#f3e8ff!important;
  padding:2px 4px!important;
}
.card-meta{
  font-size:12px!important;
  color:var(--text-3)!important;
  line-height:1.3!important;
  margin-bottom:6px!important;
}
.card-footer{
  display:flex!important;
  justify-content:space-between!important;
  align-items:center!important;
}
.card-date{
  font-size:10px!important;
  color:var(--text-3)!important;
  font-weight:500!important;
}
.card-active-indicator{
  color:var(--accent)!important;
  font-size:12px!important;
  animation:pulse 2s ease-in-out infinite!important;
}

/* ── Buttons ── */
.stButton>button{border-radius:8px;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:6px 14px;font-weight:500;font-size:14px;width:100%;transition:all .12s;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.stButton>button:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-bg)}
div[data-testid="stFormSubmitButton"]>button{background:var(--accent)!important;color:#fff!important;border:none!important;font-weight:600!important;box-shadow:0 1px 3px rgba(37,99,235,.2)}
div[data-testid="stFormSubmitButton"]>button:hover{background:#1d4ed8!important}

/* ── Chat Messages (clean, modern) ── */
.stChatMessage{
  border:none!important;
  border-radius:0!important;
  margin-bottom:0!important;
  padding:16px 0!important;
  border-bottom:1px solid var(--border-light)!important;
  background:transparent!important;
}
.stChatMessage:last-child{border-bottom:none!important}
.stChatMessage p,.stChatMessage span,.stChatMessage div,.stChatMessage li{
  color:var(--text)!important;font-size:15px!important;line-height:1.7!important;
}
.stChatMessage code{font-size:13px!important}
.stChatMessage [data-testid="chatAvatarIcon-user"]{
  background:var(--accent)!important;
}
.stChatMessage [data-testid="chatAvatarIcon-assistant"]{
  background:#1a1d21!important;
}

/* Chat input bar */
.stChatInput{
  position:sticky;
  bottom:0;
  z-index:100;
  padding:12px 0;
}
.stChatInput>div{
  border:1.5px solid var(--border)!important;
  border-radius:12px!important;
  background:var(--surface)!important;
  box-shadow:0 2px 12px rgba(0,0,0,.06)!important;
  padding:4px!important;
}
.stChatInput textarea{
  font-size:14.5px!important;
}

/* ── Cards ── */
.c{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.02)}
.c-l{font-size:11px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--text-3)!important;margin-bottom:5px}
.c-v{font-size:16px;font-weight:500;color:var(--text)!important;line-height:1.45;word-break:break-word}

/* ── Section Headers ── */
.sh{font-size:11px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--text-3)!important;margin:22px 0 10px 0}
.sh-small{font-size:13px;font-weight:600;color:var(--text)!important;margin:14px 0 6px 0;padding-bottom:4px;border-bottom:1px solid var(--border-light)}

/* ── Badges ── */
.b{display:inline-block!important;padding:3px 8px!important;border-radius:20px!important;font-size:10px!important;font-weight:600!important;letter-spacing:.3px!important;text-transform:uppercase!important;line-height:1.2!important;white-space:nowrap!important}
.b-blue{background:var(--accent-bg)!important;color:#1d4ed8!important}
.b-green{background:var(--success-bg)!important;color:var(--success)!important}
.b-yellow{background:var(--warn-bg)!important;color:var(--warn)!important}
.b-red{background:var(--danger-bg)!important;color:var(--danger)!important}
.b-gray{background:#f3f4f6!important;color:var(--text-2)!important}

/* ── Research Block ── */
.rb{background:#fafbfc;border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;padding:14px 18px;font-size:13px;line-height:1.75;color:var(--text-2)!important;max-height:380px;overflow-y:auto}

/* ── Pipeline Cards ── */
.pc{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 12px;margin-bottom:6px;transition:all .15s ease;font-size:13px!important;line-height:1.4!important}
.pc:hover{border-color:var(--accent);box-shadow:0 2px 4px rgba(0,0,0,.05);transform:translateX(2px)}
.pc-content{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}

/* ── Metric Cards (mx) - pipeline & dashboard stats ── */
.mx{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 8px;text-align:center;min-width:100px;display:flex;flex-direction:column;justify-content:center;height:100%}
.mx .mx-n{font-size:22px!important;font-weight:700!important;color:var(--accent)!important;line-height:1.1!important}
.mx .mx-l{font-size:10px!important;font-weight:600!important;letter-spacing:.5px!important;text-transform:uppercase!important;color:var(--text-3)!important;margin-top:4px!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}

/* ── Memory Indicator ── */
.mem-indicator{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:500}
.mem-active{background:#dcfce7;color:#166534}.mem-stale{background:#fef3c7;color:#92400e}

/* ── Timeline ── */
.tl-item{position:relative;padding-left:24px;margin-bottom:12px}
.tl-item::before{content:'';position:absolute;left:6px;top:8px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.tl-item::after{content:'';position:absolute;left:9px;top:20px;width:2px;height:calc(100% + 4px);background:var(--border)}
.tl-item:last-child::after{display:none}

/* ── Alerts ── */
.alert-card{background:var(--warn-bg);border:1px solid #fde68a;border-radius:var(--radius);padding:12px 16px;margin-bottom:8px;position:relative}
.alert-danger{background:var(--danger-bg);border-color:#fca5a5;position:relative}
/* Tooltip on hover */
.has-tooltip{cursor:help}
.has-tooltip::after{
  content:attr(data-tooltip);
  position:absolute;
  bottom:100%;
  left:0;
  background:rgba(26,29,33,0.95);
  color:#fff;
  padding:8px 12px;
  border-radius:6px;
  font-size:12px;
  line-height:1.4;
  white-space:normal;
  max-width:400px;
  width:max-content;
  z-index:1000;
  opacity:0;
  pointer-events:none;
  transition:opacity .2s ease;
  margin-bottom:8px;
  box-shadow:0 4px 12px rgba(0,0,0,0.2);
}
.has-tooltip::before{
  content:'';
  position:absolute;
  bottom:100%;
  left:20px;
  border:6px solid transparent;
  border-top-color:rgba(26,29,33,0.95);
  opacity:0;
  pointer-events:none;
  transition:opacity .2s ease;
  margin-bottom:2px;
}
.has-tooltip:hover::after,
.has-tooltip:hover::before{
  opacity:1;
}

/* ── Expander ── */
.streamlit-expanderHeader{
  background:var(--surface)!important;
  border:1px solid var(--border)!important;
  border-radius:8px!important;
  padding:12px 16px!important;
  font-size:14px!important;
  transition:all .2s ease!important;
}
.streamlit-expanderHeader:hover{
  border-color:var(--accent)!important;
  background:var(--accent-bg)!important;
}
/* Alert expanders - special styling */
.stExpander:has(.streamlit-expanderHeader:contains("⚠️")){
  margin-bottom:8px;
}
.stExpander:has(.streamlit-expanderHeader:contains("⚠️")) .streamlit-expanderHeader{
  background:var(--danger-bg)!important;
  border-color:#fca5a5!important;
}
.stExpander:has(.streamlit-expanderHeader:contains("📅")) .streamlit-expanderHeader{
  background:var(--warn-bg)!important;
  border-color:#fde68a!important;
}
.streamlit-expanderContent{
  border:1px solid var(--border)!important;
  border-top:none!important;
  border-radius:0 0 8px 8px!important;
  padding:12px 16px!important;
  background:var(--surface)!important;
}
[data-testid="stForm"]{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px}

/* ── Sidebar Chat History ── */
.sidebar-session{
  padding:10px 12px;margin-bottom:4px;border-radius:8px;cursor:pointer;
  transition:background .1s;border:1px solid transparent;
}
.sidebar-session:hover{background:var(--accent-bg);border-color:var(--border)}
.sidebar-session.active{background:var(--accent-bg);border-color:var(--accent)}
.sidebar-session .ss-domain{font-size:13px;font-weight:500;color:var(--text)!important}
.sidebar-session .ss-contact{font-size:11px;color:var(--text-3)!important;margin-top:2px}
.sidebar-session .ss-meta{font-size:10px;color:var(--text-3)!important;margin-top:3px}

/* ── Quick Action Buttons ── */
.qa-btn{
  background:var(--surface)!important;border:1.5px solid var(--border)!important;
  border-radius:20px!important;padding:6px 16px!important;font-size:12px!important;
  font-weight:500!important;transition:all .15s!important;
}
.qa-btn:hover{border-color:var(--accent)!important;background:var(--accent-bg)!important;color:var(--accent)!important}

/* ── Brief Card ── */
.brief-card{
  background:linear-gradient(135deg,#f8faff,#eef3ff);
  border:1px solid #c7d2fe;border-left:4px solid var(--accent);
  border-radius:0 var(--radius) var(--radius) 0;
  padding:18px 22px;margin-bottom:16px;
}
.brief-card .brief-line{font-size:14px;line-height:1.7;color:var(--text)!important;margin-bottom:4px}

/* ── Misc ── */
.stAlert{border-radius:8px!important}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:#d1d5db;border-radius:3px}
.stDownloadButton>button{background:#f9fafb!important;color:var(--text)!important;border:1px solid var(--border)!important}
a{color:var(--accent)!important;text-decoration:none!important}a:hover{text-decoration:underline!important}

/* ── Advisor History Dropdown ── */
.stSelectbox [data-baseweb="select"] > div{
  border:1px solid var(--border)!important;
  border-radius:6px!important;
  background:var(--surface)!important;
  font-size:13px!important;
  min-height:38px!important;
  padding:0 12px!important;
}
.stSelectbox [data-baseweb="select"] > div:hover{border-color:var(--accent)!important}
.stSelectbox label{
  font-size:12px!important;
  font-weight:600!important;
  color:var(--text-2)!important;
  text-transform:uppercase!important;
  letter-spacing:0.5px!important;
}

/* ── Chat Messages ── */
.stChatMessage{
  border-radius:8px!important;
  padding:16px!important;
  margin:12px 0!important;
}
.stChatMessage[data-testid*="user"]{
  background:rgba(37,99,235,.05)!important;
  border-left:3px solid var(--accent)!important;
}
.stChatMessage[data-testid*="assistant"]{
  background:var(--surface)!important;
  border-left:3px solid rgba(100,116,139,.3)!important;
}
.stChatMessage p{
  font-size:15px!important;
  line-height:1.6!important;
}
.streamlit-expanderContent p{font-size:14px!important;line-height:1.6!important}
</style>
""", unsafe_allow_html=True)


# ── Component Helpers ─────────────────────────────────────────

def bdg(text, color="gray"):
    return f"<span class='b b-{color}'>{text}</span>"

def crd(label, value):
    return f"<div class='c'><div class='c-l'>{label}</div><div class='c-v'>{value or '—'}</div></div>"

def mcard(number, label):
    return f"<div class='mx'><div class='mx-n'>{number}</div><div class='mx-l'>{label}</div></div>"

def sh(text):
    return f"<div class='sh'>{text}</div>"

def sh_small(text):
    """Normal-size section header for Intelligence / dense UIs."""
    return f"<div class='sh-small'>{text}</div>"

def tl_item(date, desc, badge_text=None, badge_color="blue"):
    b = f" {bdg(badge_text, badge_color)}" if badge_text else ""
    return (
        f"<div class='tl-item'>"
        f"<div style='font-size:11px;color:var(--text-3);'>{date}</div>"
        f"<div style='font-size:13px;margin-top:3px;'>{desc}{b}</div>"
        f"</div>"
    )

def alert(text, level="warn", full_text=None):
    cls = "alert-danger" if level == "danger" else "alert-card"
    if full_text and len(full_text) > len(text):
        escaped_full = full_text.replace("'", "&apos;").replace('"', "&quot;")
        return "<div class='" + cls + " has-tooltip' data-tooltip='" + escaped_full + "'>" + text + "</div>"
    return "<div class='" + cls + "'>" + text + "</div>"


def fmt_v(v):
    if not v:
        return "—"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


# ── Permissions ───────────────────────────────────────────────

def has_perm(perm):
    u = st.session_state.get("user")
    if not u:
        return False
    return ROLE_PERMISSIONS.get(u.get("role", "viewer"), {}).get(perm, False)