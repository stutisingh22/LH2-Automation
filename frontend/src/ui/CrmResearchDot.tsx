/** Green / red dot for publisher CRM rows: deep research saved vs not. */
export function CrmResearchDot({ researched }: { researched?: boolean }) {
  const yes = Boolean(researched)
  return (
    <span
      className={`crm-research-dot${yes ? ' crm-research-dot--yes' : ' crm-research-dot--no'}`}
      title={yes ? 'Researched' : 'Not researched'}
      aria-label={yes ? 'Researched' : 'Not researched'}
    />
  )
}
