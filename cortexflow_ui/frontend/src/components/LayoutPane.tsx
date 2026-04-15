import type { ReactNode } from 'react'
import './LayoutPane.css'

type LayoutPaneProps = {
  children: ReactNode
}

export function LayoutPane({ children }: LayoutPaneProps) {
  return <div className="layout-pane">{children}</div>
}
