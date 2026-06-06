/**
 * SplitButton component tests.
 *
 *   - Primary click fires the primary handler
 *   - Chevron click opens the dropdown
 *   - Clicking a dropdown option fires its handler + closes the menu
 *   - Outside click closes the menu without firing anything
 *   - Esc closes the menu
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SplitButton from './SplitButton'


describe('SplitButton', () => {

  it('primary click fires the primary handler (not the dropdown)', () => {
    const onPrimary = vi.fn()
    const onA = vi.fn()
    render(
      <SplitButton
        primary={{ label: 'Default', onClick: onPrimary }}
        options={[{ label: 'A', onClick: onA }]}
      />
    )
    fireEvent.click(screen.getByText('Default'))
    expect(onPrimary).toHaveBeenCalledTimes(1)
    expect(onA).not.toHaveBeenCalled()
  })

  it('chevron opens dropdown; option click fires + closes', async () => {
    const onPrimary = vi.fn()
    const onA = vi.fn()
    const onB = vi.fn()
    render(
      <SplitButton
        primary={{ label: 'Default', onClick: onPrimary }}
        options={[
          { label: 'Option A', onClick: onA },
          { label: 'Option B', onClick: onB },
        ]}
      />
    )
    // Dropdown not open yet
    expect(screen.queryByText('Option A')).toBeNull()
    // Open via chevron
    fireEvent.click(screen.getByTitle('More actions'))
    expect(screen.getByText('Option A')).toBeTruthy()
    // Click an option
    fireEvent.click(screen.getByText('Option B'))
    expect(onB).toHaveBeenCalledTimes(1)
    expect(onPrimary).not.toHaveBeenCalled()
    // Menu closed
    await waitFor(() => expect(screen.queryByText('Option A')).toBeNull())
  })

  it('Esc closes the menu', async () => {
    render(
      <SplitButton
        primary={{ label: 'P', onClick: () => {} }}
        options={[{ label: 'X', onClick: () => {} }]}
      />
    )
    fireEvent.click(screen.getByTitle('More actions'))
    expect(screen.getByText('X')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByText('X')).toBeNull())
  })

  it('outside click closes the menu', async () => {
    render(
      <div>
        <SplitButton
          primary={{ label: 'P', onClick: () => {} }}
          options={[{ label: 'X', onClick: () => {} }]}
        />
        <span data-testid="outside">outside</span>
      </div>
    )
    fireEvent.click(screen.getByTitle('More actions'))
    expect(screen.getByText('X')).toBeTruthy()
    fireEvent.click(screen.getByTestId('outside'))
    await waitFor(() => expect(screen.queryByText('X')).toBeNull())
  })

  it('renders option descriptions when provided', () => {
    render(
      <SplitButton
        primary={{ label: 'P', onClick: () => {} }}
        options={[
          { label: 'A', description: 'A description', onClick: () => {} },
          { label: 'B', onClick: () => {} },
        ]}
      />
    )
    fireEvent.click(screen.getByTitle('More actions'))
    expect(screen.getByText('A description')).toBeTruthy()
  })
})
