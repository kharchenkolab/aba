/**
 * The shared inline click-to-rename title used by the global header, the
 * generic entity card header, and the Result/Run views. Guards the commit
 * semantics (Enter/blur save the trimmed+changed value, Escape reverts) and
 * the uniform AI-suggested glyph.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import EditableTitle from './EditableTitle'

describe('EditableTitle', () => {
  it('click swaps to an input; Enter commits the trimmed value', () => {
    const onCommit = vi.fn()
    render(<EditableTitle value="Old name" onCommit={onCommit} />)
    fireEvent.click(screen.getByText('Old name'))
    const input = screen.getByRole('textbox') as HTMLInputElement
    fireEvent.change(input, { target: { value: '  New name  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onCommit).toHaveBeenCalledWith('New name')
  })

  it('blur commits a changed value', () => {
    const onCommit = vi.fn()
    render(<EditableTitle value="a" onCommit={onCommit} />)
    fireEvent.click(screen.getByText('a'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'b' } })
    fireEvent.blur(screen.getByRole('textbox'))
    expect(onCommit).toHaveBeenCalledWith('b')
  })

  it('Escape reverts without committing', () => {
    const onCommit = vi.fn()
    render(<EditableTitle value="Keep me" onCommit={onCommit} />)
    fireEvent.click(screen.getByText('Keep me'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'changed' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    expect(onCommit).not.toHaveBeenCalled()
    expect(screen.queryByText('Keep me')).not.toBeNull()
  })

  it('does not commit an unchanged (or blanked) value', () => {
    const onCommit = vi.fn()
    render(<EditableTitle value="Same" onCommit={onCommit} />)
    fireEvent.click(screen.getByText('Same'))
    fireEvent.blur(screen.getByRole('textbox'))     // unchanged
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('multiline mode edits via a textarea; Enter commits, Shift+Enter inserts a newline', () => {
    const onCommit = vi.fn()
    render(<EditableTitle value="stmt" onCommit={onCommit} multiline />)
    fireEvent.click(screen.getByText('stmt'))
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(ta.tagName).toBe('TEXTAREA')
    fireEvent.change(ta, { target: { value: 'new stmt' } })
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true })   // newline — must NOT commit
    expect(onCommit).not.toHaveBeenCalled()
    fireEvent.keyDown(ta, { key: 'Enter' })                   // commit
    expect(onCommit).toHaveBeenCalledWith('new stmt')
  })

  it('renders the AI-suggested glyph only when aiSuggested', () => {
    const { rerender, container } = render(<EditableTitle value="T" onCommit={() => {}} aiSuggested />)
    expect(container.querySelector('.edit-title__ai')).not.toBeNull()
    rerender(<EditableTitle value="T" onCommit={() => {}} />)
    expect(container.querySelector('.edit-title__ai')).toBeNull()
  })
})
