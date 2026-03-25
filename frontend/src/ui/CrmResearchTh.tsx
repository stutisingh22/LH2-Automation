import { Microscope } from 'lucide-react'

/** Table header cell: research indicator column (icon only). */
export function CrmResearchTh() {
  return (
    <th className="crm-research-col" scope="col" aria-label="Deep research saved">
      <span className="crm-research-col-icon">
        <Microscope size={16} strokeWidth={1.85} aria-hidden />
      </span>
    </th>
  )
}
