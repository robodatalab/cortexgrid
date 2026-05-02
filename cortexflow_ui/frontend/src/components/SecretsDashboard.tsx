import { useCallback, useEffect, useState } from 'react'
import { Check, Trash2 } from 'lucide-react'
import './SecretsDashboard.css'

type Secret = { id: string; value: string }

type Row = {
  originalId: string | null
  originalValue: string
  id: string
  value: string
}

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; rows: Row[] }
  | { status: 'error'; message: string }

function toRow(s: Secret): Row {
  return { originalId: s.id, originalValue: s.value, id: s.id, value: s.value }
}

function isDirty(r: Row): boolean {
  return r.id !== r.originalId || r.value !== r.originalValue
}

function useSecrets() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })
  const load = useCallback(() => {
    fetch('/api/secrets')
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: Secret[]) => setState({ status: 'ready', rows: data.map(toRow) }))
      .catch((err: unknown) =>
        setState({
          status: 'error',
          message: err instanceof Error ? err.message : String(err),
        }),
      )
  }, [])
  useEffect(() => {
    load()
  }, [load])
  return { state, setState, load }
}

type SecretRowProps = {
  row: Row
  onChange: (patch: Partial<Row>) => void
  onSave: () => void
  onDelete: () => void
}

function SecretRow({ row, onChange, onSave, onDelete }: SecretRowProps) {
  const canSave = isDirty(row) && row.id.length > 0
  return (
    <div className="secret-row">
      <input
        className="secret-row__id"
        type="text"
        value={row.id}
        onChange={(e) => onChange({ id: e.target.value })}
        placeholder="Name"
      />
      <input
        className="secret-row__value"
        type="text"
        value={row.value}
        onChange={(e) => onChange({ value: e.target.value })}
        placeholder="Value"
      />
      <button
        type="button"
        className="btn btn--icon"
        onClick={onSave}
        disabled={!canSave}
        aria-label="Save"
      >
        <Check size={16} />
      </button>
      <button
        type="button"
        className="btn btn--icon"
        onClick={onDelete}
        aria-label="Delete"
      >
        <Trash2 size={16} />
      </button>
    </div>
  )
}

type ConfirmModalProps = {
  message: string
  onConfirm: () => void
  onCancel: () => void
}

function ConfirmModal({ message, onConfirm, onCancel }: ConfirmModalProps) {
  return (
    <div
      className="secrets-modal__backdrop"
      role="dialog"
      aria-modal="true"
      onClick={onCancel}
    >
      <div className="secrets-modal" onClick={(e) => e.stopPropagation()}>
        <p className="secrets-modal__message">{message}</p>
        <div className="secrets-modal__actions">
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={onConfirm}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

function useRowActions(state: LoadState, setState: (s: LoadState) => void) {
  const updateRow = (index: number, patch: Partial<Row>) => {
    if (state.status !== 'ready') return
    setState({
      status: 'ready',
      rows: state.rows.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    })
  }
  const addRow = () => {
    if (state.status !== 'ready') return
    setState({
      status: 'ready',
      rows: [
        { originalId: null, originalValue: '', id: '', value: '' },
        ...state.rows,
      ],
    })
  }
  const saveRow = async (index: number) => {
    if (state.status !== 'ready') return
    const r = state.rows[index]
    if (!r.id) return
    if (r.originalId && r.originalId !== r.id) {
      const res = await fetch(`/api/secrets/${encodeURIComponent(r.originalId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        alert(`Rename failed (delete old): HTTP ${res.status}\n${await res.text()}`)
        return
      }
    }
    const res = await fetch(`/api/secrets/${encodeURIComponent(r.id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: r.value }),
    })
    if (!res.ok) {
      alert(`Save failed: HTTP ${res.status}\n${await res.text()}`)
      return
    }
    setState({
      status: 'ready',
      rows: state.rows.map((row, i) =>
        i === index ? { ...row, originalId: row.id, originalValue: row.value } : row,
      ),
    })
  }
  const deleteRow = async (index: number) => {
    if (state.status !== 'ready') return
    const r = state.rows[index]
    if (r.originalId !== null) {
      const res = await fetch(`/api/secrets/${encodeURIComponent(r.originalId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const body = await res.text()
        alert(`Delete failed: HTTP ${res.status}\n${body}`)
        return
      }
    }
    setState({ status: 'ready', rows: state.rows.filter((_, i) => i !== index) })
  }
  return { updateRow, addRow, saveRow, deleteRow }
}

export function SecretsDashboard() {
  const { state, setState } = useSecrets()
  const { updateRow, addRow, saveRow, deleteRow } = useRowActions(state, setState)
  const [pendingDelete, setPendingDelete] = useState<number | null>(null)
  const requestDelete = (index: number) => {
    if (state.status !== 'ready') return
    if (state.rows[index].originalId === null) {
      deleteRow(index)
      return
    }
    setPendingDelete(index)
  }
  if (state.status === 'loading') {
    return <div className="secrets-dashboard__empty">Loading…</div>
  }
  if (state.status === 'error') {
    return <div className="secrets-dashboard__empty">Error: {state.message}</div>
  }
  const pendingRow = pendingDelete !== null ? state.rows[pendingDelete] : null
  return (
    <div className="secrets-dashboard">
      <header className="secrets-dashboard__header">
        <h1>Secrets</h1>
        <button type="button" className="btn secrets-dashboard__add" onClick={addRow}>
          + New secret
        </button>
      </header>
      <div className="secrets-dashboard__list">
        {state.rows.map((r, i) => (
          <SecretRow
            key={r.originalId ?? `new-${i}`}
            row={r}
            onChange={(patch) => updateRow(i, patch)}
            onSave={() => saveRow(i)}
            onDelete={() => requestDelete(i)}
          />
        ))}
      </div>
      {pendingRow && pendingDelete !== null && (
        <ConfirmModal
          message={`Delete secret "${pendingRow.originalId}"? This cannot be undone.`}
          onConfirm={() => {
            const idx = pendingDelete
            setPendingDelete(null)
            deleteRow(idx)
          }}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}
