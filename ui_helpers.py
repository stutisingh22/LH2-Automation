"""
ui_helpers.py — CSS injection, badges, cards, formatting helpers

Design: Intentional light theme. Clean, calm, professional, enterprise-grade.
Linear/Stripe/Vercel-inspired. High contrast, minimal color, no SaaS fluff.
"""

import streamlit as st
from config import ROLE_PERMISSIONS


# ── Full name display (use wherever full name is shown) ─────────
FULL_NAME_DISPLAY_OVERRIDES = {"Stuti Gupta": "Stuti Singh"}


def display_full_name(username):
    """Return the display full name; overrides e.g. Stuti Gupta → Stuti Singh."""
    u = (username or "").strip()
    return FULL_NAME_DISPLAY_OVERRIDES.get(u, u) if u else (username or "")


# ── CSS ───────────────────────────────────────────────────────

def inject_css():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap');

/* ── Root: Intentional light system ── */
:root{
  --bg:#f6f8fb;
  --surface:#ffffff;
  --surface-2:#f2f4f8;
  --surface-hover:#eef1f6;
  --border:#e2e8f0;
  --border-strong:#d0d7e2;
  --text:#0f172a;
  --text-2:#475569;
  --muted:#94a3b8;
  --accent:#2563eb;
  --accent-subtle:rgba(37,99,235,0.06);
  --success:#16a34a;
  --warn:#d97706;
  --danger:#dc2626;
  --radius:4px;
}
.stApp{background:var(--bg)!important;color:var(--text)!important;font-family:'Inter',-apple-system,system-ui,sans-serif!important}
#MainMenu,footer,header{visibility:hidden}
.block-container{padding:16px 32px 32px!important;max-width:1400px!important;gap:8px!important}
p,li,span,div,label,.stMarkdown,td,th{color:var(--text)!important;font-size:14px!important;line-height:1.5!important}

/* Sidebar: always visible */
[data-testid="collapsedControl"]{display:none!important;visibility:hidden!important;pointer-events:none!important}
button[kind="header"]{display:none!important}
section[data-testid="stSidebar"]{min-width:21rem!important;max-width:21rem!important;transform:none!important;background:var(--surface)!important;border-right:1px solid var(--border)!important}
section[data-testid="stSidebar"] > div{width:21rem!important;padding-top:16px!important}
section[data-testid="stSidebar"][aria-expanded="false"]{transform:translateX(0)!important}
/* Hide default Streamlit multipage nav (apps / page list) so only custom sidebar content shows */
[data-testid="stSidebarNav"]{display:none!important}

/* ── Tabs: transparent, 1px bottom border, active 2px accent ── */
.stTabs [data-baseweb="tab-list"]{
  gap:0!important;
  background:transparent!important;
  border-bottom:1px solid #e2e8f0!important;
  padding:0 1rem!important;
  margin-bottom:20px!important;
}
.stTabs [data-baseweb="tab"]{
  height:40px!important;
  font-size:12px!important;
  font-weight:500!important;
  letter-spacing:1px!important;
  text-transform:uppercase!important;
  color:var(--text-2)!important;
  background:transparent!important;
  border:none!important;
  border-bottom:2px solid transparent!important;
  border-radius:0!important;
  padding:0 1rem!important;
  margin-bottom:-1px!important;
}
.stTabs [data-baseweb="tab"]:hover{color:var(--text)!important}
.stTabs [aria-selected="true"]{
  color:var(--text)!important;
  font-weight:600!important;
  border-bottom-color:var(--accent)!important;
}
.stTabs [data-baseweb="tab"]:focus{outline:none!important}
.stTabs [data-baseweb="tab-panel"]{padding-top:0!important}

/* ── Dashboard header ── */
.dashboard-header-left{display:flex;align-items:center;padding:6px 0 0 0}
.logo-container{display:flex;align-items:center;gap:8px}
.logo-diamond{font-size:16px;color:var(--accent)}
.logo-text{font-size:11px;font-weight:700;letter-spacing:1.5px;color:var(--accent)}
.logo-subtitle{font-size:10px;font-weight:500;letter-spacing:0.5px;color:var(--text-2);margin-left:8px;padding-left:8px;border-left:1px solid var(--border)}
.breadcrumb-container{display:flex;align-items:center;gap:6px;padding:6px 12px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.breadcrumb-item{font-size:11px;font-weight:600;color:var(--text-2)}
.breadcrumb-sep{color:var(--muted);opacity:0.8}
.dashboard-header-right{display:flex;justify-content:flex-end;align-items:center;padding:2px 0 0 0;min-height:24px}
.user-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.user-avatar{width:18px;height:18px;background:var(--surface-2);color:var(--text-2);display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:600;flex-shrink:0;border-radius:50%}
.user-info{display:flex;flex-direction:column;align-items:flex-start;gap:0;line-height:1.2}
.user-badge .user-name{font-size:9px!important;font-weight:600!important;color:var(--text)!important}
.user-badge .user-role{font-size:8px!important;font-weight:500!important;color:var(--text-2)!important;text-transform:uppercase!important;letter-spacing:0.5px!important}

/* ── Strategist score block (structured, dot indicator) ── */
.score-block{display:flex;flex-direction:column;align-items:flex-end;gap:2px;padding:8px 0}
.score-block .score-label{font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--text-2)}
.score-block .score-value{display:flex;align-items:center;gap:6px;font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:600;color:var(--text)}
.score-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.score-dot--red{background:#dc2626}
.score-dot--amber{background:#d97706}
.score-dot--green{background:#16a34a}

/* ── Inputs: calm, native feel ── */
.stTextInput>div>div>input,.stTextArea>div>div>textarea{background:var(--surface)!important;border:1px solid var(--border-strong)!important;color:var(--text)!important;border-radius:var(--radius)!important;font-size:14px!important;padding:8px 12px!important}
.stTextInput>div>div>input:focus,.stTextArea>div>div>textarea:focus{border-color:var(--accent)!important;box-shadow:0 0 0 2px rgba(37,99,235,0.08)!important}
.stSelectbox>div>div{background:var(--surface)!important;border:1px solid var(--border-strong)!important;border-radius:var(--radius)!important}
.stSelectbox label,.stTextInput label,.stTextArea label{color:var(--text-2)!important;font-weight:500!important;font-size:11px!important}

/* ── Sidebar: intentional light, no pills ── */
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)){position:relative!important;margin-bottom:4px!important}
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)) > div:nth-child(2){position:absolute!important;top:0!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;height:100%!important;z-index:5!important}
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)) > div:nth-child(2) .stButton{width:100%!important;height:100%!important}
[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(> div:nth-child(2)) > div:nth-child(2) .stButton>button{width:100%!important;height:100%!important;background:transparent!important;border:none!important;opacity:0!important;cursor:pointer!important;padding:0!important}
.sidebar-card{background:transparent!important;border:1px solid transparent!important;border-radius:0!important;padding:8px 10px!important;margin-bottom:4px!important;transition:background .1s!important;position:relative!important}
.sidebar-card:hover{background:var(--surface-2)!important}
.sidebar-card.active{background:#eef2ff!important;border-left:3px solid var(--accent)!important}
[data-testid="stSidebar"] .stButton{width:100%!important}
[data-testid="stSidebar"] .stButton > button[kind="secondary"]{width:100%!important;height:28px!important;min-height:28px!important;padding:4px!important;margin:0 0 8px 0!important;background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:var(--radius)!important;color:var(--text-2)!important;font-size:11px!important;font-weight:500!important;text-transform:uppercase!important}
[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover{background:var(--surface-2)!important;border-color:var(--border-strong)!important;color:var(--text)!important}
.card-header{display:flex!important;justify-content:space-between!important;align-items:flex-start!important;margin-bottom:4px!important;gap:4px!important}
.card-domain{font-size:13px!important;font-weight:600!important;color:var(--text)!important;line-height:1.2!important;flex:1!important}
.card-badges{display:flex!important;gap:4px!important;flex-wrap:wrap!important;justify-content:flex-end!important}
.session-badge{display:inline-block!important;font-size:9px!important;font-weight:600!important;letter-spacing:0.5px!important;text-transform:uppercase!important;color:var(--muted)!important;background:transparent!important;border:none!important}
.badge-new{color:var(--muted)!important}
.badge-active{color:var(--accent)!important}
.badge-ongoing{color:var(--muted)!important}
.badge-memory{color:var(--muted)!important}
.card-meta{font-size:11px!important;color:var(--text-2)!important;line-height:1.3!important;margin-bottom:4px!important}
.card-footer{display:flex!important;justify-content:space-between!important;align-items:center!important}
.card-date{font-size:10px!important;color:var(--text-2)!important;font-weight:500!important}

/* ── Buttons: primary vs secondary ── */
.stButton>button{border-radius:var(--radius);background:var(--surface);color:var(--text);border:1px solid var(--border-strong);padding:6px 12px;font-weight:500;font-size:13px;width:100%;transition:border-color .1s,background .1s,color .1s}
.stButton>button:hover{background:var(--surface-2)!important;border-color:var(--border)!important}
div[data-testid="stFormSubmitButton"]>button{background:var(--accent)!important;color:#fff!important;border:none!important;font-weight:600!important}
div[data-testid="stFormSubmitButton"]>button:hover{background:#1d4ed8!important}

/* ── Cards .c: flat panel, subtle depth ── */
.c{background:var(--surface);border:1px solid var(--border);border-left:2px solid var(--border);padding:12px;margin-bottom:8px;border-radius:var(--radius)}
.c-l{font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:var(--text-2)!important;margin-bottom:4px}
.c-v{font-size:14px;font-weight:500;color:var(--text)!important;line-height:1.4;word-break:break-word}

/* ── Section headers: 12px uppercase, border-bottom ── */
.sh{font-size:12px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#64748b!important;margin:20px 0 8px 0;border-bottom:1px solid #e2e8f0;padding-bottom:6px}
.sh-small{font-size:12px;font-weight:600;color:var(--text)!important;margin:12px 0 6px 0}

/* ── Badges .b: inline text, no pill ── */
.b{display:inline!important;font-size:10px!important;font-weight:600!important;letter-spacing:0.5px!important;text-transform:uppercase!important;line-height:1.2!important;white-space:nowrap!important;background:transparent!important;border:none!important;padding:0!important}
.b-blue{color:var(--accent)!important}
.b-green{color:var(--success)!important}
.b-yellow{color:var(--warn)!important}
.b-red{color:var(--danger)!important}
.b-gray{color:var(--text-2)!important}

/* ── KPI strip: light, no shadow ── */
.kpi-strip{display:flex;flex-wrap:nowrap;border:1px solid #e2e8f0;background:var(--surface);margin-bottom:8px;border-radius:var(--radius)}
.kpi-segment{flex:1;padding:14px 0;border-right:1px solid #e2e8f0;text-align:center}
.kpi-segment:last-child{border-right:none}
.kpi-segment-n{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:600;color:#0f172a;line-height:1.2}
.kpi-segment-l{font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#64748b;margin-top:4px}

/* Legacy .mx single card ── */
.mx{background:var(--surface);border:1px solid var(--border);padding:12px 8px;text-align:center;min-width:80px;border-radius:var(--radius)}
.mx .mx-n{font-family:'JetBrains Mono',monospace;font-size:22px!important;font-weight:600!important;color:#0f172a!important;line-height:1.1!important}
.mx .mx-l{font-size:11px!important;font-weight:600!important;letter-spacing:1px!important;text-transform:uppercase!important;color:#64748b!important;margin-top:4px!important}

/* ── Research block ── */
.rb{background:var(--surface);border:1px solid var(--border);border-left:2px solid var(--accent);padding:12px 14px;font-size:13px;line-height:1.6;color:var(--text-2)!important;max-height:380px;overflow-y:auto;border-radius:var(--radius)}

/* ── Pipeline cards .pc: company rows style ── */
.pc{background:var(--surface);border:1px solid #e2e8f0;padding:12px 16px;margin-bottom:6px;font-size:13px!important;line-height:1.4!important;border-radius:var(--radius);transition:background .1s}
.pc:hover{background:var(--surface-2)!important}
.pc-content{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}

/* ── Timeline ── */
.tl-item{position:relative;padding-left:20px;margin-bottom:8px}
.tl-item::before{content:'';position:absolute;left:4px;top:6px;width:6px;height:6px;background:var(--accent);border-radius:50%}
.tl-item::after{content:'';position:absolute;left:6px;top:16px;width:1px;height:calc(100% + 4px);background:var(--border)}
.tl-item:last-child::after{display:none}

/* ── Contact timeline (per-contact merged view) ── */
.timeline-wrap{margin-top:8px}
.timeline-item{display:flex;align-items:flex-start;gap:10px;padding:10px;margin-bottom:6px;background:var(--surface);border:1px solid #e2e8f0;border-left:3px solid #cbd5e1;border-radius:var(--radius)}
.timeline-item--outreach{border-left-color:#3b82f6}
.timeline-item--follow-up{border-left-color:#8b5cf6}
.timeline-item--meeting{border-left-color:var(--success)}
.timeline-item--note{border-left-color:#64748b}
.timeline-item--reply{border-left-color:#059669}
.timeline-item--message{border-left-color:var(--accent)}
.timeline-item--outcome{border-left-color:var(--warn)}
.timeline-item--ai-insight{border-left-color:#7c3aed}
.timeline-item--ai-summary{border-left-color:#6366f1}
.timeline-item--drive{border-left-color:#0ea5e9}
.timeline-item--research,.timeline-item--other{border-left-color:var(--text-2)}
.timeline-meta{font-size:10px;color:var(--muted);white-space:nowrap;flex-shrink:0;font-family:'JetBrains Mono',monospace}
.timeline-body{flex:1;min-width:0}
.timeline-type{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:var(--text-2);margin-bottom:4px}
.timeline-summary{font-size:13px;color:#0f172a;line-height:1.45}

/* ── Alerts: left stripe only, no full border ── */
.alert-card{background:#fff7ed;border:none;border-left:3px solid #f59e0b;padding:10px 14px;margin-bottom:8px;border-radius:0;color:#92400e!important}
.alert-danger{background:#fef2f2;border:none;border-left:3px solid #dc2626;padding:10px 14px;margin-bottom:8px;border-radius:0;color:#991b1b!important}
.has-tooltip{cursor:help}
.has-tooltip::after{content:attr(data-tooltip);position:absolute;bottom:100%;left:0;background:var(--surface);color:var(--text);padding:8px 12px;border:1px solid var(--border);font-size:12px;line-height:1.4;white-space:normal;max-width:400px;z-index:1000;opacity:0;pointer-events:none;transition:opacity .15s;margin-bottom:4px;border-radius:var(--radius);box-shadow:0 4px 12px rgba(0,0,0,.08)}
.has-tooltip::before{content:'';position:absolute;bottom:100%;left:20px;border:6px solid transparent;border-top-color:var(--surface);opacity:0;pointer-events:none;transition:opacity .15s;margin-bottom:2px}
.has-tooltip:hover::after,.has-tooltip:hover::before{opacity:1}

/* ── Expander ── */
.streamlit-expanderHeader{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:var(--radius)!important;padding:10px 14px!important;font-size:13px!important}
.streamlit-expanderHeader:hover{border-color:var(--border-strong)!important;background:var(--surface-2)!important}
.streamlit-expanderContent{border:1px solid var(--border)!important;border-top:none!important;border-radius:0 var(--radius) var(--radius)!important;padding:10px 14px!important;background:var(--surface)!important}
[data-testid="stForm"]{background:var(--surface);border:1px solid var(--border);padding:16px;border-radius:var(--radius)}

/* ── Chat: light blue user, white assistant ── */
.stChatMessage{border:none!important;border-radius:0!important;margin-bottom:0!important;padding:12px 0!important;border-bottom:1px solid var(--border)!important;background:transparent!important}
.stChatMessage:last-child{border-bottom:none!important}
.stChatMessage p,.stChatMessage span,.stChatMessage div,.stChatMessage li{color:var(--text)!important;font-size:14px!important;line-height:1.6!important}
.stChatMessage[data-testid*="user"]{border-left:3px solid var(--accent)!important;padding-left:12px!important;background:#eef2ff!important}
.stChatMessage[data-testid*="assistant"]{border-left:3px solid #cbd5e1!important;padding-left:12px!important;background:var(--surface)!important}
.stChatInput>div{border:1px solid var(--border-strong)!important;border-radius:var(--radius)!important;background:var(--surface)!important;padding:4px!important}
.streamlit-expanderContent p{font-size:13px!important;line-height:1.5!important}

/* ── Brief card ── */
.brief-card{background:var(--surface);border:1px solid var(--border);border-left:2px solid var(--accent);padding:14px 18px;margin-bottom:8px;border-radius:var(--radius)}
.brief-card.brief-card--compact{margin-bottom:6px;padding:12px 16px}
.brief-card .brief-line{font-size:13px;line-height:1.6;color:var(--text)!important;margin-bottom:4px}
.brief-card table,.markdown-rendered table,.research-table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}
.brief-card th,.brief-card td,.markdown-rendered th,.markdown-rendered td,.research-table th,.research-table td{border:1px solid var(--border);padding:8px 12px;text-align:left;vertical-align:top}
.brief-card th,.markdown-rendered th,.research-table th{background:var(--surface-2);font-weight:600;color:var(--text)!important}
.research-table thead tr{border-bottom:2px solid var(--border-strong)}
.research-table tbody tr:nth-child(even){background:var(--surface-2)}
.brief-card p,.markdown-rendered p{margin:0 0 8px 0;line-height:1.5}
.brief-card p:last-child,.markdown-rendered p:last-child{margin-bottom:0}

/* ── Companies tab: company rows + expanded contact block ── */
.company-row{background:var(--surface);border:1px solid #e2e8f0;padding:12px 16px;border-radius:var(--radius)}
.company-row:hover{background:var(--surface-2)!important}
.contact-expander-block{background:#f8fafc;border-top:1px solid #e2e8f0;padding:12px}

/* ── Sidebar session / quick action ── */
.sidebar-session{padding:8px 10px;margin-bottom:2px;cursor:pointer;transition:background .1s;border:1px solid transparent;border-radius:var(--radius)}
.sidebar-session:hover{background:var(--surface-2);border-color:var(--border)}
.sidebar-session.active{background:#eef2ff;border-left:3px solid var(--accent)}
.sidebar-session .ss-domain{font-size:12px;font-weight:500;color:var(--text)!important}
.sidebar-session .ss-contact{font-size:11px;color:var(--text-2)!important;margin-top:2px}
.sidebar-session .ss-meta{font-size:10px;color:var(--text-2)!important;margin-top:2px}
.qa-btn{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:var(--radius)!important;padding:6px 12px!important;font-size:12px!important;font-weight:500!important}
.qa-btn:hover{background:var(--surface-2)!important;border-color:var(--border-strong)!important;color:var(--text)!important}

/* ── LH2 KPI/status/pipeline (light) ── */
.lh2-kpi-strip{display:flex!important;flex-wrap:nowrap!important;overflow-x:auto!important;gap:0!important;border:1px solid #e2e8f0!important;margin-bottom:8px!important;background:var(--surface)!important;min-height:56px!important;border-radius:var(--radius)!important}
.lh2-kpi-cell{min-width:100px!important;flex:1 1 auto!important;border-right:1px solid #e2e8f0!important;padding:10px 12px!important;text-align:left!important}
.lh2-kpi-cell:last-child{border-right:none!important}
.lh2-kpi-cell .lh2-kpi-n{font-family:'JetBrains Mono',monospace;font-size:22px!important;font-weight:600!important;color:#0f172a!important;line-height:1.2!important}
.lh2-kpi-cell .lh2-kpi-l{font-size:11px!important;font-weight:600!important;letter-spacing:.08em!important;text-transform:uppercase!important;color:#64748b!important;margin-top:4px!important}
.lh2-status-grid{display:flex!important;flex-wrap:nowrap!important;gap:0!important;border:1px solid #e2e8f0!important;margin-bottom:8px!important;background:var(--surface)!important;border-radius:var(--radius)!important}
.lh2-status-cell{min-width:70px!important;flex:1 1 auto!important;border-right:1px solid #e2e8f0!important;padding:8px 10px!important;text-align:center!important}
.lh2-status-cell:last-child{border-right:none!important}
.lh2-status-cell .lh2-status-n{font-family:'JetBrains Mono',monospace;font-size:16px!important;font-weight:600!important;color:#0f172a!important}
.lh2-status-cell .lh2-status-l{font-size:10px!important;text-transform:uppercase!important;color:#64748b!important;margin-top:4px!important;border-bottom:1px solid #e2e8f0!important;display:inline-block!important;padding-bottom:2px!important}
.lh2-pipeline-row{display:block!important;border:1px solid #e2e8f0!important;padding:12px 16px!important;margin-bottom:4px!important;background:var(--surface)!important;font-size:13px!important;border-radius:var(--radius)!important}
.lh2-pipeline-row:hover{background:var(--surface-2)!important}
.lh2-sh{font-size:12px!important;font-weight:600!important;letter-spacing:1.5px!important;text-transform:uppercase!important;color:#64748b!important;margin:20px 0 8px!important;border-bottom:1px solid #e2e8f0!important;padding-bottom:6px!important}
.mem-indicator{display:inline-flex;align-items:center;gap:4px;font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-2)}
.mem-active{color:var(--success)}
.mem-stale{color:var(--warn)}

/* ── Misc ── */
.stAlert{border-radius:var(--radius)!important}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.stDownloadButton>button{background:var(--surface)!important;color:var(--text)!important;border:1px solid var(--border)!important;border-radius:var(--radius)!important}
a{color:var(--accent)!important;text-decoration:none!important}
a:hover{text-decoration:underline!important}
.stSelectbox [data-baseweb="select"] > div{border:1px solid var(--border-strong)!important;border-radius:var(--radius)!important;background:var(--surface)!important;font-size:13px!important;min-height:36px!important;padding:0 10px!important}
.stSelectbox [data-baseweb="select"] > div:hover{border-color:var(--border)!important}

/* ── Sidebar logo: inline with main header, compact ── */
.sidebar-logo{display:block;padding:0 0 20px 0;text-align:left}
.sidebar-logo img,.sidebar-logo + div img{max-height:32px;width:auto!important;object-fit:contain}
.sidebar-logo + div{display:flex!important;justify-content:flex-start!important;align-items:center!important;margin-left:0!important}

/* ── Sequence step card ── */
[data-testid="stVerticalBlock"]:has(.sequence-step-card){background:#ffffff;border:1px solid #e2e8f0;padding:16px;margin-bottom:16px;border-radius:6px}
.sequence-step-card{display:none}
.sequence-step-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.sequence-step-divider{height:1px;background:#e2e8f0;margin:12px 0}

/* ── SPOC Tracker cards ── */
.spoc-card{padding:2px 0}
.spoc-card-company{font-size:11px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;color:#64748b!important}
.spoc-card-name{font-size:14px;font-weight:600;color:#0f172a!important;margin-top:1px}
.spoc-card-role{font-size:12px!important;font-weight:400!important;color:#64748b!important}
.spoc-card-contacts{font-size:12px!important;color:#475569!important;margin-top:3px;line-height:1.7}
.spoc-card-contacts a{color:#2563eb!important;text-decoration:none!important}
.spoc-card-contacts a:hover{text-decoration:underline!important}
.spoc-card-next{font-size:12px!important;color:#475569!important;padding-top:6px}
.spoc-card-overdue{font-size:12px!important;color:#b85450!important;padding-top:6px}
.spoc-detail{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;margin:4px 0 14px 0}
</style>
""", unsafe_allow_html=True)


# ── Markdown tables → proper HTML (no separator row) ─────────

def _parse_md_table_line(line):
    """Split a pipe table line into cells. Line must start and end with |."""
    line = (line or "").strip()
    if not line or not line.startswith("|") or not line.endswith("|"):
        return None
    parts = [p.strip() for p in line[1:-1].split("|")]
    return parts


def _is_separator_line(line):
    """True if line looks like |---||---| (markdown table separator)."""
    if not line or not line.strip().startswith("|"):
        return False
    inner = line.strip()[1:-1].replace(" ", "")
    return len(inner) > 0 and all(c in "-:" for c in inner)


def markdown_tables_to_html(text):
    """
    Replace markdown pipe tables in text with proper HTML <table> (thead + tbody).
    No separator row in output — professional tables only.
    Returns the string with table blocks replaced by HTML. Use with st.markdown(..., unsafe_allow_html=True).
    """
    import re
    if not (text or "").strip():
        return text or ""
    lines = (text or "").split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Start of a potential table: line looks like | a | b | c |
        if re.match(r"^\s*\|.+\|\s*$", line):
            table_lines = []
            j = i
            while j < len(lines) and re.match(r"^\s*\|.+\|\s*$", lines[j]):
                table_lines.append(lines[j])
                j += 1
            # Parse: first line = header, next line if separator skip, rest = body
            if len(table_lines) >= 2:
                header_cells = _parse_md_table_line(table_lines[0])
                if header_cells:
                    body_start = 1
                    if len(table_lines) > 1 and _is_separator_line(table_lines[1]):
                        body_start = 2
                    body_rows = []
                    for r in range(body_start, len(table_lines)):
                        cells = _parse_md_table_line(table_lines[r])
                        if cells and len(cells) == len(header_cells):
                            body_rows.append(cells)
                    if body_rows or body_start == 2:
                        def esc(s):
                            return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                        html = ['<table class="research-table">']
                        html.append("<thead><tr>")
                        for c in header_cells:
                            html.append(f"<th>{esc(c)}</th>")
                        html.append("</tr></thead>")
                        if body_rows:
                            html.append("<tbody>")
                            for row in body_rows:
                                html.append("<tr>")
                                for c in row:
                                    html.append(f"<td>{esc(c)}</td>")
                                html.append("</tr>")
                            html.append("</tbody>")
                        html.append("</table>")
                        out.append("".join(html))
                        i = j
                        continue
        out.append(line)
        i += 1
    return "\n".join(out)


def render_research_with_proper_tables(text):
    """
    Convert markdown tables in text to HTML tables, then return the result
    for use with st.markdown(..., unsafe_allow_html=True). Rest of text stays as-is
    (Streamlit will render markdown around the HTML).
    """
    if not (text or "").strip():
        return "(No content)"
    return markdown_tables_to_html(text)


# ── Option B: HTML as stored format ─────────────────────────────

def is_html_content(s):
    """Return True if s looks like HTML (e.g. stored research_full from Option B)."""
    if not (s or "").strip():
        return False
    s = (s or "").strip()
    return s.lstrip().startswith("<") and ("</" in s or "/>" in s)


def html_to_plain_text(html, max_length=None):
    """
    Strip HTML to plain text for Drive Docs API, AI context, etc.
    Preserves newlines for block structure (e.g. after </p>, </tr>, </li>).
    """
    if not (html or "").strip():
        return ""
    import re
    text = (html or "").strip()
    # Block elements → newline
    text = re.sub(r"</(p|div|tr|li|h[1-6]|br)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = text.strip()
    if max_length and len(text) > max_length:
        text = text[:max_length] + "\n\n…"
    return text


def research_display_to_editor_html(text):
    """
    Convert research_full from DB to HTML for the main (editable) view.
    If already HTML, return as-is. Else treat as markdown and convert to full HTML.
    """
    if not (text or "").strip():
        return ""
    s = (text or "").strip()
    if is_html_content(s):
        return s
    return markdown_to_html(s)


def wysiwyg_editor_html_page(initial_html):
    """
    Build a self-contained HTML page for WYSIWYG editing (contenteditable).
    When the user clicks "Done editing", the page posts the editable div's innerHTML
    to the parent via postMessage, which streamlit-iframe-event can capture.
    Returns full document as string. initial_html is base64-encoded in the page to avoid escaping issues.
    """
    import base64
    payload = base64.b64encode((initial_html or "").encode("utf-8")).decode("ascii")
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 12px; background: #fff; color: #1a1a1a; font-size: 14px; line-height: 1.5; }
  #ed { min-height: 320px; padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px; outline: none; }
  #ed:focus { border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
  #ed table { border-collapse: collapse; width: 100%%; margin: 8px 0; }
  #ed th, #ed td { border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }
  #ed th { background: #f1f5f9; font-weight: 600; }
  #ed h1, #ed h2, #ed h3 { margin: 12px 0 6px; }
  .done { margin-top: 12px; padding: 8px 16px; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
  .done:hover { background: #1d4ed8; }
</style></head>
<body>
  <div id="ed" contenteditable="true"></div>
  <button class="done" id="btn">Done editing</button>
  <div id="p" data-c="%s" style="display:none"></div>
  <script>
    (function() {
      var p = document.getElementById("p");
      var data = p.getAttribute("data-c") || "";
      try {
        if (data) {
          var raw = atob(data);
          var bytes = new Uint8Array([].map.call(raw, function(c) { return c.charCodeAt(0); }));
          var dec = (typeof TextDecoder !== "undefined")
            ? new TextDecoder().decode(bytes)
            : decodeURIComponent(escape(raw));
          document.getElementById("ed").innerHTML = dec;
        } else { document.getElementById("ed").innerHTML = "<p>(No content)</p>"; }
      } catch (e) { document.getElementById("ed").innerHTML = "<p>(No content)</p>"; }
      document.getElementById("btn").onclick = function() {
        var html = document.getElementById("ed").innerHTML;
        if (window.parent && window.parent !== window)
          window.parent.postMessage(html, "*");
      };
    })();
  </script>
</body></html>""" % payload


# ── Markdown (tables) rendering ─────────────────────────────

def markdown_to_html(text, max_length=None):
    """Convert markdown (including pipe tables) to HTML. Safe for embedding in card divs."""
    if not (text or "").strip():
        return ""
    s = (text or "").strip()
    if max_length and len(s) > max_length:
        s = s[:max_length] + "\n\n…"
    try:
        import markdown
        return markdown.markdown(s, extensions=["tables"])
    except Exception:
        return (s or "").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def render_brief_card_html(text, compact=True):
    """Return HTML for a brief card with markdown (and tables) rendered. Use with st.markdown(..., unsafe_allow_html=True)."""
    if not (text or "").strip():
        return "<div class='brief-card brief-card--compact' style='color:var(--text-2);'>(No content)</div>"
    inner = markdown_to_html(text)
    cls = "brief-card brief-card--compact" if compact else "brief-card"
    return f"<div class='{cls}'>{inner}</div>"


# ── Component Helpers ─────────────────────────────────────────

def bdg(text, color="gray"):
    return f"<span class='b b-{color}'>{text}</span>"

def crd(label, value):
    return f"<div class='c'><div class='c-l'>{label}</div><div class='c-v'>{value or '—'}</div></div>"

def mcard(number, label):
    return f"<div class='kpi-segment'><div class='kpi-segment-n'>{number}</div><div class='kpi-segment-l'>{label}</div></div>"

def lh2_kpi_cell(number, label):
    return f"<div class='lh2-kpi-cell'><div class='lh2-kpi-n'>{number}</div><div class='lh2-kpi-l'>{label}</div></div>"

def lh2_status_cell(value, label):
    return f"<div class='lh2-status-cell'><div class='lh2-status-n'>{value}</div><div class='lh2-status-l'>{label}</div></div>"

def lh2_section_header(text):
    return f"<div class='lh2-sh'>{text}</div>"

def lh2_pipeline_row(name, domain, score_text):
    return f"<div class='lh2-pipeline-row'><div style='font-weight:500;'>{name}</div><div style='font-size:11px;color:var(--text-2);'>{domain}</div><div style='font-size:11px;color:var(--text-2);'>{score_text}</div></div>"

def sh(text):
    return f"<div class='sh'>{text}</div>"

def sh_small(text):
    return f"<div class='sh-small'>{text}</div>"

def tl_item(date, desc, badge_text=None, badge_color="blue"):
    b = f" {bdg(badge_text, badge_color)}" if badge_text else ""
    return (
        f"<div class='tl-item'>"
        f"<div style='font-size:10px;color:var(--text-2);'>{date}</div>"
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


# ── Contact timeline ───────────────────────────────────────────

def _timeline_escape(s):
    if not s:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;")[:500])

def timeline_item_html(ts_display, item_type, channel, status, summary, status_colors=None):
    type_class = (item_type or "other").lower().replace(" ", "-").replace("_", "-")
    if type_class == "ai-insight":
        type_class = "ai-insight"
    elif type_class == "ai-summary":
        type_class = "ai-summary"
    elif type_class not in (
        "outreach", "follow-up", "meeting", "note", "reply", "message",
        "outcome", "drive", "research", "other"
    ):
        type_class = "other"
    cls = f"timeline-item timeline-item--{type_class}"
    time_str = ts_display or "—"
    ch_str = (channel or "—").strip()
    status_badge = ""
    if status and status_colors:
        c = status_colors.get(status, "gray")
        status_badge = f" {bdg(status, c)}"
    summary_safe = _timeline_escape(summary)
    return (
        f"<div class='{cls}'>"
        f"<div class='timeline-meta'>{time_str}<br>{ch_str}{status_badge}</div>"
        f"<div class='timeline-body'>"
        f"<div class='timeline-type'>{_timeline_escape(item_type)}</div>"
        f"<div class='timeline-summary'>{summary_safe}</div>"
        f"</div></div>"
    )


# ── Permissions ───────────────────────────────────────────────

def has_perm(perm):
    u = st.session_state.get("user")
    if not u:
        return False
    return ROLE_PERMISSIONS.get(u.get("role", "viewer"), {}).get(perm, False)
