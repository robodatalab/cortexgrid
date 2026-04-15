import type { ReactNode } from 'react'
import './TitledFrame.css'

type TitledFrameProps = {
  title: string
  titleClassName?: string
  children: ReactNode
}

export function TitledFrame({ title, titleClassName, children }: TitledFrameProps) {
  const className = titleClassName
    ? `titled-frame__title ${titleClassName}`
    : 'titled-frame__title'
  return (
    <div className="titled-frame">
      <div className={className}>{title}</div>
      <div className="titled-frame__body">{children}</div>
    </div>
  )
}
