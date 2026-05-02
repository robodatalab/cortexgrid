import { useState } from 'react'
import { useStreamList } from '../useStreamList'
import './RunNotesPanel.css'

type Note = {
  id: string
  body: string
  created_at: string
  updated_at: string
}

type RowProps = {
  note: Note
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

  if (editing) {
    return (
      <div className="run-notes__item">
        <textarea
          className="run-notes__textarea"
          aria-label="Edit note"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="run-notes__actions">
          <button type="button" className="btn" onClick={save}>
            Save
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setEditing(false)}
          >
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="run-notes__item">
      <div className="run-notes__body">{note.body}</div>
      <div className="run-notes__meta">
        {new Date(note.updated_at).toLocaleString()}
      </div>
      <div className="run-notes__actions">
        <button type="button" className="btn" onClick={startEdit}>
          Edit
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => onDelete(note.id)}
        >
          Delete
        </button>
      </div>
    </div>
  )
}

type Props = {
  runName: string
}

export function RunNotesPanel({ runName }: Props) {
  const notes = useStreamList<Note>(
    `/api/runs/${runName}/notes/stream`,
    (n) => n.id,
  )
  const [draft, setDraft] = useState('')

  const sorted = Object.values(notes).sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )

  async function add() {
    const body = draft.trim()
    if (!body) return
    await fetch(`/api/runs/${runName}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    })
    setDraft('')
  }

  async function edit(id: string, body: string) {
    await fetch(`/api/notes/run/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    })
  }

  async function remove(id: string) {
    await fetch(`/api/notes/run/${id}`, { method: 'DELETE' })
  }

  return (
    <div className="run-notes">
      <div className="run-notes__title">Notes</div>
      <div className="run-notes__list">
        {sorted.length === 0 && (
          <div className="run-notes__empty">No notes yet</div>
        )}
        {sorted.map((note) => (
          <NoteRow key={note.id} note={note} onEdit={edit} onDelete={remove} />
        ))}
      </div>
      <div className="run-notes__compose">
        <textarea
          className="run-notes__textarea"
          placeholder="Add a note..."
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button
          type="button"
          className="btn"
          onClick={add}
          disabled={!draft.trim()}
        >
          Add
        </button>
      </div>
    </div>
  )
}
