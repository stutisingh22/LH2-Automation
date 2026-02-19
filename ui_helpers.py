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
.block-container{padding:1rem 2rem 2rem;max-width:1400px}
p,li,span,div,label,.stMarkdown,td,th{color:var(--text)!important}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"]{gap:0;background:var(--surface);border-bottom:1px solid var(--border);padding:0 1rem;border-radius:var(--radius) var(--radius) 0 0}
.stTabs [data-baseweb="tab"]{height:44px;font-size:11px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--text-3)!important;background:transparent!important;border:none!important;border-bottom:2px solid transparent!important;padding:0 1rem;transition:all .12s}
.stTabs [aria-selected="true"]{color:var(--text)!important;border-bottom-color:var(--accent)!important}
.stTabs [data-baseweb="tab"]:hover{color:var(--text-2)!important}

/* ── Section nav: single radio so selected/unselected same size ── */
.nav-tab-bar-wrap{
  background:var(--surface);
  border-bottom:1px solid var(--border);
  padding:0 0.25rem;
  margin:0 -0.25rem 0.75rem 0;
  border-radius:var(--radius) var(--radius) 0 0;
}
/* Section nav radio: compact tab look, same font for all options */
.nav-tab-bar-wrap + div [data-testid="stRadio"] [role="radiogroup"],
.block-container [data-testid="stRadio"]:first-of-type [role="radiogroup"]{
  display:flex!important;
  flex-wrap:nowrap!important;
  gap:0!important;
  border-bottom:none!important;
  padding:0!important;
}
.nav-tab-bar-wrap + div [data-testid="stRadio"] [role="radiogroup"] label,
.block-container [data-testid="stRadio"]:first-of-type [role="radiogroup"] label{
  font-size:7px!important;
  font-weight:500!important;
  letter-spacing:.4px!important;
  text-transform:uppercase!important;
  color:var(--text-3)!important;
  padding:0 0.35rem!important;
  height:22px!important;
  line-height:22px!important;
  min-width:0!important;
  flex:1 1 auto!important;
  white-space:nowrap!important;
  overflow:visible!important;
  border-bottom:none!important;
  transition:color .12s!important;
}
.nav-tab-bar-wrap + div [data-testid="stRadio"] [role="radiogroup"] label:hover,
.block-container [data-testid="stRadio"]:first-of-type [role="radiogroup"] label:hover{
  color:var(--text-2)!important;
}
.nav-tab-bar-wrap + div [data-testid="stRadio"] [role="radiogroup"] label:has(input:checked),
.block-container [data-testid="stRadio"]:first-of-type [role="radiogroup"] label:has(input:checked){
  color:var(--accent)!important;
  font-size:7px!important;
  font-weight:500!important;
}
/* Hide radio circles so it looks like tabs */
.nav-tab-bar-wrap + div [data-testid="stRadio"] [role="radiogroup"] input[type="radio"],
.block-container [data-testid="stRadio"]:first-of-type [role="radiogroup"] input[type="radio"]{
  position:absolute!important;
  opacity:0!important;
  width:0!important;
  height:0!important;
}
.nav-tab-bar-wrap + div [data-testid="stRadio"] [role="radiogroup"] > div,
.block-container [data-testid="stRadio"]:first-of-type [role="radiogroup"] > div{
  display:flex!important;
  align-items:stretch!important;
  flex:1!important;
  min-width:0!important;
}

/* ── Inputs ── */
.stTextInput>div>div>input,.stTextArea>div>div>textarea{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--text)!important;border-radius:8px!important;font-size:14px!important;padding:10px 14px!important}
.stTextInput>div>div>input:focus,.stTextArea>div>div>textarea:focus{border-color:var(--accent)!important;box-shadow:0 0 0 3px rgba(37,99,235,.06)!important}
.stSelectbox>div>div{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:8px!important}
.stSelectbox label,.stTextInput label,.stTextArea label{color:var(--text-2)!important;font-weight:500!important;font-size:12px!important;letter-spacing:.3px}

/* ── Sidebar: smaller buttons, clearer context/contact text ── */
[data-testid="stSidebar"] .stButton>button{
  padding:5px 12px!important;
  font-size:11px!important;
  font-weight:500!important;
  min-height:auto!important;
}
[data-testid="stSidebar"] .sidebar-session-block{font-size:13px!important;font-weight:500!important;color:var(--text)!important}
[data-testid="stSidebar"] .sidebar-session-meta{font-size:12px!important;color:var(--text-2)!important}

/* ── Buttons ── */
.stButton>button{border-radius:8px;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:6px 14px;font-weight:500;font-size:12px;width:100%;transition:all .12s;box-shadow:0 1px 2px rgba(0,0,0,.03)}
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
  color:var(--text)!important;font-size:14px!important;line-height:1.7!important;
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
.c-l{font-size:10px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:var(--text-3)!important;margin-bottom:5px}
.c-v{font-size:14px;font-weight:500;color:var(--text)!important;line-height:1.45;word-break:break-word}

/* ── Section Headers ── */
.sh{font-size:11px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--text-3)!important;margin:22px 0 10px 0}
.sh-small{font-size:13px;font-weight:600;color:var(--text)!important;margin:14px 0 6px 0;padding-bottom:4px;border-bottom:1px solid var(--border-light)}

/* ── Badges ── */
.b{display:inline-block;padding:3px 10px;border-radius:20px;font-size:10.5px;font-weight:600;letter-spacing:.4px;text-transform:uppercase}
.b-blue{background:var(--accent-bg);color:#1d4ed8}.b-green{background:var(--success-bg);color:var(--success)}
.b-yellow{background:var(--warn-bg);color:var(--warn)}.b-red{background:var(--danger-bg);color:var(--danger)}
.b-gray{background:#f3f4f6;color:var(--text-2)}

/* ── Research Block ── */
.rb{background:#fafbfc;border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;padding:14px 18px;font-size:13px;line-height:1.75;color:var(--text-2)!important;max-height:380px;overflow-y:auto}

/* ── Pipeline Cards ── */
.pc{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:5px;transition:border-color .1s}.pc:hover{border-color:var(--accent)}

/* ── Metric Cards ── */
.mx{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px;text-align:center}
.mx-n{font-size:26px;font-weight:700;color:var(--text)}.mx-l{font-size:10px;font-weight:500;letter-spacing:1.2px;text-transform:uppercase;color:var(--text-3);margin-top:4px}

/* ── Memory Indicator ── */
.mem-indicator{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:500}
.mem-active{background:#dcfce7;color:#166534}.mem-stale{background:#fef3c7;color:#92400e}

/* ── Timeline ── */
.tl-item{position:relative;padding-left:24px;margin-bottom:12px}
.tl-item::before{content:'';position:absolute;left:6px;top:8px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.tl-item::after{content:'';position:absolute;left:9px;top:20px;width:2px;height:calc(100% + 4px);background:var(--border)}
.tl-item:last-child::after{display:none}

/* ── Alerts ── */
.alert-card{background:var(--warn-bg);border:1px solid #fde68a;border-radius:var(--radius);padding:12px 16px;margin-bottom:8px}
.alert-danger{background:var(--danger-bg);border-color:#fca5a5}

/* ── Expander ── */
.streamlit-expanderHeader{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:8px!important}
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
.brief-card .brief-line{font-size:13px;line-height:1.7;color:var(--text)!important;margin-bottom:4px}

/* ── Misc ── */
.stAlert{border-radius:8px!important}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:#d1d5db;border-radius:3px}
.stDownloadButton>button{background:#f9fafb!important;color:var(--text)!important;border:1px solid var(--border)!important}
a{color:var(--accent)!important;text-decoration:none!important}a:hover{text-decoration:underline!important}
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

def alert(text, level="warn"):
    cls = "alert-danger" if level == "danger" else "alert-card"
    return f"<div class='{cls}'>{text}</div>"

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