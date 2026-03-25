/** Server-rendered research HTML (tables, headings) — Streamlit `research-doc-view` + horizontal scroll. */
export function ResearchHtmlView({ html, className = '' }: { html: string; className?: string }) {
  if (!html.trim()) return null
  return (
    <div className={`lh2-table-wrap research-html-wrap ${className}`.trim()}>
      <div className="research-doc-view" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}
