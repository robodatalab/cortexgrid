import { useState } from 'react'
import { useStreamList } from '../useStreamList'
import './ExperimentDashboard.css'

type RunNote = {
  id: string
  run_name: string
  body: string
  created_at: string
  updated_at: string
}

type ExperimentNote = {
  id: string
  experiment_name: string
  body: string
  created_at: string
  updated_at: string
}

type CombinedNote = RunNote | ExperimentNote

function isRunNote(note: CombinedNote): note is RunNote {
  return 'run_name' in note
}

type RowProps = {
  note: CombinedNote
  onEdit: (id: string, body: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

function NoteRow({ note, onEdit, onDelete }: RowProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(note.body)

  async function save() {
    const body = draft.trim()
    if (!body) return
    await onEdit(note.id, body)
    setEditing(false)
  }

  function startEdit() {
    setDraft(note.body)
    setEditing(true)
  }

  const label = isRunNote(note) ? `Run: ${note.run_name}` : 'Experiment note'
  const kind = isRunNote(note) ? 'run' : 'experiment'

  if (editing) {
    return (
      <div className="experiment-notes__item experiment-notes__item--experiment">
        <div className="experiment-notes__label">{label}</div>
        <textarea
          className="experiment-notes__textarea"
          aria-label="Edit note"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="experiment-notes__actions">
          <button
            type="button"
            className="experiment-notes__button"
            onClick={save}
          >
            Save
          </button>
          <button
            type="button"
            className="experiment-notes__button"
            onClick={() => setEditing(false)}
          >
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className={'experiment-notes__item experiment-notes__item--' + kind}
    >
      <div className="experiment-notes__label">{label}</div>
      <div className="experiment-notes__body">{note.body}</div>
      <div className="experiment-notes__meta">
        {new Date(note.updated_at).toLocaleString()}
      </div>
      {!isRunNote(note) && (
        <div className="experiment-notes__actions">
          <button
            type="button"
            className="experiment-notes__button"
            onClick={startEdit}
          >
            Edit
          </button>
          <button
            type="button"
            className="experiment-notes__button"
            onClick={() => onDelete(note.id)}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

type Props = {
  experimentName: string
}

export function ExperimentDashboard({ experimentName }: Props) {
  const encodedName = encodeURIComponent(experimentName)
  const notes = useStreamList<CombinedNote>(
    `/api/experiments/${encodedName}/notes/stream`,
  )
  const [draft, setDraft] = useState('')

  const sorted = Object.values(notes).sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )

  async function add() {
    const body = draft.trim()
    if (!body) return
    await fetch(`/api/experiments/${encodedName}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    })
    setDraft('')
  }

  async function edit(id: string, body: string) {
    await fetch(`/api/notes/experiment/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    })
  }

  async function remove(id: string) {
    await fetch(`/api/notes/experiment/${id}`, { method: 'DELETE' })
  }

  return (
    <div className="experiment-dashboard">
      <div className="experiment-dashboard__title">{experimentName}</div>
      <div className="experiment-notes">
        <div className="experiment-notes__list">
          {sorted.length === 0 && (
            <div className="experiment-notes__empty">No notes yet</div>
          )}
          {sorted.map((note) => (
            <NoteRow
              key={note.id}
              note={note}
              onEdit={edit}
              onDelete={remove}
            />
          ))}
        </div>
        <div className="experiment-notes__compose">
          <textarea
            className="experiment-notes__textarea"
            placeholder="Add an experiment note..."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button
            type="button"
            className="experiment-notes__button"
            onClick={add}
            disabled={!draft.trim()}
          >
            Add
          </button>
        </div>
      </div>
    </div>
  )
}
