import { useEffect, useMemo, useState } from 'react'
import { Allotment } from 'allotment'
import 'allotment/dist/style.css'
import './App.css'
import { LayoutPane } from './components/LayoutPane'
import { ConfirmModal } from './components/ConfirmModal'
import { ExperimentTree } from './components/ExperimentTree'
import type { ExperimentRun, Selection } from './components/ExperimentTree'
import { ExperimentDashboard } from './components/ExperimentDashboard'
import { RunDashboard } from './components/RunDashboard'
import type { Job } from './components/RunDashboard'
import { JobDashboard } from './components/JobDashboard'
import { JobsDashboard } from './components/JobsDashboard'
import type { JobRow } from './components/JobsDashboard'
import { InfraDashboard } from './components/InfraDashboard'
import { SecretsDashboard } from './components/SecretsDashboard'
import { IconRail } from './components/IconRail'
import type { RailView } from './components/IconRail'
import { ModelsTree } from './components/ModelsTree'
import type {
  Deployment,
  Model,
  ModelConfig,
  ModelRequirements,
  ModelSelection,
} from './components/ModelsTree'
import { DeploymentsTree } from './components/DeploymentsTree'
import { useDeploymentLoads } from './deploymentLoad'
import { pendingBundleUpdates } from './bundleUpdate'
import type { DeploymentSelection } from './components/DeploymentsTree'
import { deploymentApiUrl, deploymentId, modelId } from './ids'
import { ModelDashboard } from './components/ModelDashboard'
import { DeploymentDashboard } from './components/DeploymentDashboard'
import { useStreamList } from './useStreamList'

type Dashboard = {
  id: string
  url: string
}

type ExperimentMeta = {
  name: string
  created_at_ms: number | null
}

type PendingDelete =
  | { kind: 'experiment'; experiment_name: string }
  | { kind: 'run'; run_id: string; run_name: string }
  | { kind: 'model'; model: Model }
  | { kind: 'model-family'; family: string }

function App() {
  const [dashboards, setDashboards] = useState<Dashboard[]>([])
  const [selection, setSelection] = useState<Selection | null>(null)
  const [modelsSelection, setModelsSelection] = useState<
    ModelSelection | DeploymentSelection | null
  >(null)
  const [view, setView] = useState<RailView>('experiments')
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)

  const experimentsByName = useStreamList<ExperimentMeta>(
    '/api/experiments/meta/stream',
    (e) => e.name,
  )

  const modelsById = useStreamList<Model>('/api/models/stream', (m) => m.id)
  const models = useMemo(() => Object.values(modelsById), [modelsById])

  const deploymentsById = useStreamList<Deployment>(
    '/api/deployments/stream',
    (d) => deploymentId(d.key),
  )

  const selectedExperimentName = selection?.experiment_name ?? null
  const runsByName = useStreamList<ExperimentRun>(
    selectedExperimentName
      ? `/api/experiments/${encodeURIComponent(selectedExperimentName)}/runs/stream`
      : null,
    (r) => r.run_name,
  )

  function deleteUrl(target: PendingDelete): string {
    switch (target.kind) {
      case 'experiment':
        return `/api/experiments/${encodeURIComponent(target.experiment_name)}`
      case 'run':
        return `/api/runs/${encodeURIComponent(target.run_id)}`
      case 'model':
        return `/api/models/${encodeURIComponent(target.model.family)}/${encodeURIComponent(target.model.suffix)}/${encodeURIComponent(target.model.run_name)}`
      case 'model-family':
        return `/api/models/${encodeURIComponent(target.family)}`
    }
  }

  async function handleConfirmDelete() {
    if (pendingDelete === null) return
    const target = pendingDelete
    setPendingDelete(null)
    const res = await fetch(deleteUrl(target), { method: 'DELETE' })
    if (!res.ok) {
      alert(`Delete failed: HTTP ${res.status}\n${await res.text()}`)
      return
    }
    if (target.kind === 'experiment' && selection?.experiment_name === target.experiment_name) {
      setSelection(null)
    } else if (
      target.kind === 'run' &&
      (selection?.kind === 'run' || selection?.kind === 'job') &&
      selection.run_id === target.run_id
    ) {
      setSelection(null)
    } else if (
      target.kind === 'model' &&
      modelsSelection?.kind === 'model' &&
      modelsSelection.id === target.model.id
    ) {
      setModelsSelection(null)
    } else if (
      target.kind === 'model-family' &&
      modelsSelection?.kind === 'model' &&
      modelsById[modelsSelection.id]?.family === target.family
    ) {
      setModelsSelection(null)
    }
  }

  function pendingDeleteMessage(target: PendingDelete): string {
    switch (target.kind) {
      case 'experiment':
        return `Delete experiment "${target.experiment_name}" and all of its runs? This cannot be undone.`
      case 'run':
        return `Delete run "${target.run_name}"? This cannot be undone.`
      case 'model':
        return `Delete model "${target.model.family}/${target.model.suffix}" from run "${target.model.run_name}"? This cannot be undone.`
      case 'model-family':
        return `Delete every model in family "${target.family}"? This cannot be undone.`
    }
  }

  const experimentNames = useMemo(
    () => Object.keys(experimentsByName).sort(),
    [experimentsByName],
  )

  const runsByExperiment = useMemo(() => {
    if (selectedExperimentName === null) return {}
    const list = Object.values(runsByName).sort((a, b) =>
      a.run_name.localeCompare(b.run_name),
    )
    return { [selectedExperimentName]: list }
  }, [selectedExperimentName, runsByName])

  const activeRunId =
    selection?.kind === 'run' || selection?.kind === 'job'
      ? selection.run_id
      : null
  const activeRunJobsMap = useStreamList<Job>(
    activeRunId ? `/api/runs/${activeRunId}/jobs/stream` : null,
    (j) => j.job_id,
  )
  const activeRunJobs = activeRunId ? Object.values(activeRunJobsMap) : null

  // The cluster-wide jobs stream is polled only while its tab is open.
  const jobRowsById = useStreamList<JobRow>(
    view === 'jobs' ? '/api/jobs/stream' : null,
    (j) => j.id,
  )
  const jobRows = useMemo(() => Object.values(jobRowsById), [jobRowsById])

  const deployments = useMemo(
    () => Object.values(deploymentsById),
    [deploymentsById],
  )

  const deploymentLoads = useDeploymentLoads(
    view === 'models' ? '/api/deployments/load' : null,
  )

  const bundleUpdates = useMemo(
    () => pendingBundleUpdates(deployments, modelsById),
    [deployments, modelsById],
  )

  const selectedModel =
    modelsSelection?.kind === 'model'
      ? (modelsById[modelsSelection.id] ?? null)
      : null
  const selectedDeployment =
    modelsSelection?.kind === 'deployment'
      ? (deploymentsById[modelsSelection.id] ?? null)
      : null

  async function navigateToRun(runName: string) {
    const res = await fetch(
      `/api/runs/by-name/${encodeURIComponent(runName)}`,
    )
    if (!res.ok) {
      alert(`Could not find run "${runName}": HTTP ${res.status}`)
      return
    }
    const r = (await res.json()) as {
      experiment_name: string
      run_id: string
      run_name: string
    }
    setSelection({
      kind: 'run',
      experiment_name: r.experiment_name,
      run_id: r.run_id,
      run_name: r.run_name,
    })
    setView('experiments')
  }

  function navigateToModel(id: string) {
    setModelsSelection({ kind: 'model', id })
    setView('models')
  }

  function navigateToJob(row: JobRow) {
    setSelection({
      kind: 'job',
      experiment_name: row.experiment_name,
      run_id: row.run_id,
      job_id: row.job_id,
    })
    setView('experiments')
  }

  async function handleDeploy(model: Model) {
    const unconfiguredDeployment = {
      family: model.family,
      suffix: model.suffix,
      run_name: model.run_name,
      config_fingerprint: '',
    }
    const res = await fetch(deploymentApiUrl(unconfiguredDeployment), {
      method: 'POST',
    })
    if (!res.ok) {
      alert(`Deploy failed: HTTP ${res.status}\n${await res.text()}`)
    }
  }

  async function handleSaveRequirements(
    model: Model,
    requirements: ModelRequirements,
  ) {
    const res = await fetch(
      `/api/models/${encodeURIComponent(model.family)}/${encodeURIComponent(model.suffix)}/${encodeURIComponent(model.run_name)}/requirements`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requirements),
      },
    )
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${await res.text()}`)
    }
  }

  async function handleSaveConfig(model: Model, config: ModelConfig) {
    const res = await fetch(
      `/api/models/${encodeURIComponent(model.family)}/${encodeURIComponent(model.suffix)}/${encodeURIComponent(model.run_name)}/config`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
      },
    )
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${await res.text()}`)
    }
  }

  async function handleRedeployDeployment(deployment: Deployment) {
    const res = await fetch(deploymentApiUrl(deployment.key, 'redeploy'), {
      method: 'POST',
    })
    if (!res.ok) {
      alert(`Redeploy failed: HTTP ${res.status}\n${await res.text()}`)
    }
  }

  async function handleStopDeployment(deployment: Deployment) {
    const res = await fetch(deploymentApiUrl(deployment.key), {
      method: 'DELETE',
    })
    if (!res.ok) {
      alert(`Stop failed: HTTP ${res.status}\n${await res.text()}`)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/dashboards', { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<Dashboard[]>
      })
      .then((d) => setDashboards(d))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
      })
    return () => controller.abort()
  }, [])

  return (
    <>
      <div className="layout">
        <IconRail view={view} onSelect={setView} dashboards={dashboards} />
        <div className="layout__main">
          {view === 'infra' ? (
            <InfraDashboard />
          ) : view === 'jobs' ? (
            <JobsDashboard jobs={jobRows} onOpenJob={navigateToJob} />
          ) : view === 'secrets' ? (
            <SecretsDashboard />
          ) : view === 'models' ? (
            <Allotment>
              <Allotment.Pane preferredSize={280} minSize={180} maxSize={500}>
                <Allotment vertical>
                  <Allotment.Pane>
                    <LayoutPane>
                      <ModelsTree
                        models={models}
                        selection={
                          modelsSelection?.kind === 'model'
                            ? modelsSelection
                            : null
                        }
                        onSelect={setModelsSelection}
                        onDeleteModel={(model) =>
                          setPendingDelete({ kind: 'model', model })
                        }
                        onDeleteFamily={(family) =>
                          setPendingDelete({ kind: 'model-family', family })
                        }
                      />
                    </LayoutPane>
                  </Allotment.Pane>
                  <Allotment.Pane preferredSize={220} minSize={100}>
                    <LayoutPane>
                      <DeploymentsTree
                        deployments={deployments}
                        loads={deploymentLoads}
                        bundleUpdates={bundleUpdates}
                        selection={
                          modelsSelection?.kind === 'deployment'
                            ? modelsSelection
                            : null
                        }
                        onSelect={setModelsSelection}
                      />
                    </LayoutPane>
                  </Allotment.Pane>
                </Allotment>
              </Allotment.Pane>
              <Allotment.Pane>
                <LayoutPane>
                  {selectedModel ? (
                    <ModelDashboard
                      model={selectedModel}
                      deployments={deployments.filter(
                        (d) => modelId(d.key) === selectedModel.id,
                      )}
                      onNavigateToRun={navigateToRun}
                      onNavigateToDeployment={(id) =>
                        setModelsSelection({ kind: 'deployment', id })
                      }
                      onDeploy={handleDeploy}
                      onSaveRequirements={handleSaveRequirements}
                      onSaveConfig={handleSaveConfig}
                    />
                  ) : selectedDeployment ? (
                    <DeploymentDashboard
                      deployment={selectedDeployment}
                      modelInRepository={
                        modelsById[modelId(selectedDeployment.key)] !== undefined
                      }
                      bundleUpdate={
                        bundleUpdates[deploymentId(selectedDeployment.key)] ?? null
                      }
                      onNavigateToModel={navigateToModel}
                      onStop={handleStopDeployment}
                      onRedeploy={handleRedeployDeployment}
                    />
                  ) : (
                    <main className="main" />
                  )}
                </LayoutPane>
              </Allotment.Pane>
            </Allotment>
          ) : (
            <Allotment>
              <Allotment.Pane preferredSize={280} minSize={180} maxSize={500}>
                <LayoutPane>
                  <ExperimentTree
                    experimentNames={experimentNames}
                    runsByExperiment={runsByExperiment}
                    selection={selection}
                    onSelect={setSelection}
                    onDeleteExperiment={(experiment_name) =>
                      setPendingDelete({ kind: 'experiment', experiment_name })
                    }
                    onDeleteRun={(run_id, run_name) =>
                      setPendingDelete({ kind: 'run', run_id, run_name })
                    }
                  />
                </LayoutPane>
              </Allotment.Pane>
              <Allotment.Pane>
                <LayoutPane>
                  {selection?.kind === 'experiment' ? (
                    <ExperimentDashboard
                      experimentName={selection.experiment_name}
                      createdAtMs={
                        experimentsByName[selection.experiment_name]
                          ?.created_at_ms ?? null
                      }
                    />
                  ) : selection?.kind === 'run' ? (
                    <RunDashboard
                      runId={selection.run_id}
                      runName={selection.run_name}
                      experimentName={selection.experiment_name}
                      jobs={activeRunJobs}
                      startedAtMs={runsByName[selection.run_name]?.started_at_ms ?? null}
                      endedAtMs={runsByName[selection.run_name]?.ended_at_ms ?? null}
                      models={models}
                      onNavigateToModel={navigateToModel}
                    />
                  ) : selection?.kind === 'job' ? (
                    <JobDashboard runId={selection.run_id} jobId={selection.job_id} />
                  ) : (
                    <main className="main" />
                  )}
                </LayoutPane>
              </Allotment.Pane>
            </Allotment>
          )}
        </div>
      </div>
      {pendingDelete !== null && (
        <ConfirmModal
          message={pendingDeleteMessage(pendingDelete)}
          onConfirm={handleConfirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </>
  )
}

export default App
