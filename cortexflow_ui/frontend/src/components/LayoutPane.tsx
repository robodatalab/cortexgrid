import { ReactNode } from 'react'
import './Panel.css'

type PanelProps = {
  children: ReactNode
}

export function Panel({ children }: PanelProps) {
  return <div className="panel">{children}</div>
}
