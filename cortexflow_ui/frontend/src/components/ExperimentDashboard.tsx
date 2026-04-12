import './ExperimentDashboard.css'

type Props = {
  experimentName: string
}

export function ExperimentDashboard({ experimentName }: Props) {
  return (
    <div className="experiment-dashboard">
      <div className="experiment-dashboard__title">{experimentName}</div>
    </div>
  )
}
