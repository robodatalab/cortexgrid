import type { ReactNode } from 'react'
import './TitledFrame.css'

type TitledFrameProps = {
  title: string
  children: ReactNode
}

export function TitledFrame({ title, children }: TitledFrameProps) {
  return (
    <div className="titled-frame">
      <div className="titled-frame__title">{title}</div>
      <div className="titled-frame__body">{children}</div>
    </div>
  )
}
