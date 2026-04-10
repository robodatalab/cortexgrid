import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import App from '../../../cortexflow-ui/frontend/src/App'

describe('App', () => {
  it('renders the default scaffold heading', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: /get started/i })).toBeInTheDocument()
  })
})
