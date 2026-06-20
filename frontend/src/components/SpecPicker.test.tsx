/**
 * SpecPicker component tests.
 *
 * Covers:
 *   - Hidden when only one primary spec is registered (no choice).
 *   - Shows "Default (<env-resolved>)" + each registered spec.
 *   - Pinned value reflected in the select.
 *   - Changing the select calls onChange with the spec name.
 *   - Choosing "Default" calls onChange with "" (clear pin).
 *   - Network failure → renders nothing rather than crashing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SpecPicker from './SpecPicker'


const BOTH = {
  default: 'guide',
  specs: [
    { name: 'guide',      model: 'claude-haiku-4-5-20251001',
      prompt_mode: 'full', tool_count: null,
      summary_budget: null, is_default: true },
    { name: 'lean_guide', model: 'claude-haiku-4-5-20251001',
      prompt_mode: 'lean', tool_count: 16,
      summary_budget: 25000, is_default: false },
  ],
}

const ONLY_GUIDE = {
  default: 'guide',
  specs: [
    { name: 'guide', model: 'm', prompt_mode: 'full', tool_count: null,
      summary_budget: null, is_default: true },
  ],
}


function mockFetchOnce(body: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => body,
  })
  ;(globalThis as { fetch?: unknown }).fetch = fn
  return fn
}


describe('SpecPicker', () => {

  beforeEach(() => {
    // Default: stub fetch with the two-spec catalog.
    mockFetchOnce(BOTH)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders Backend label + a select with default + spec options', async () => {
    render(<SpecPicker pinned={null} onChange={() => {}} />)
    await waitFor(() => {
      expect(screen.getByRole('group', { name: 'Backend' })).toBeTruthy()
    })
    expect(screen.getByText(/Default \(guide\)/)).toBeTruthy()
    // Each spec rendered as an <option>; checking by text is fine.
    expect(screen.getByText(/lean_guide.*lean.*16 tools/)).toBeTruthy()
    expect(screen.getByText(/^guide.*full.*all tools$/)).toBeTruthy()
  })

  it('reflects the pinned value in the select', async () => {
    render(<SpecPicker pinned="lean_guide" onChange={() => {}} />)
    await waitFor(() => {
      const sel = screen.getByRole('combobox') as HTMLSelectElement
      expect(sel.value).toBe('lean_guide')
    })
  })

  it('null pinned selects the Default option ("" value)', async () => {
    render(<SpecPicker pinned={null} onChange={() => {}} />)
    await waitFor(() => {
      const sel = screen.getByRole('combobox') as HTMLSelectElement
      expect(sel.value).toBe('')
    })
  })

  it('changing to a spec name fires onChange with that name', async () => {
    const onChange = vi.fn()
    render(<SpecPicker pinned={null} onChange={onChange} />)
    await waitFor(() => screen.getByRole('combobox'))
    const sel = screen.getByRole('combobox') as HTMLSelectElement
    fireEvent.change(sel, { target: { value: 'lean_guide' } })
    expect(onChange).toHaveBeenCalledWith('lean_guide')
  })

  it('changing back to Default fires onChange with empty string (clear pin)', async () => {
    const onChange = vi.fn()
    render(<SpecPicker pinned="lean_guide" onChange={onChange} />)
    await waitFor(() => screen.getByRole('combobox'))
    const sel = screen.getByRole('combobox') as HTMLSelectElement
    fireEvent.change(sel, { target: { value: '' } })
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('renders nothing when only one primary spec is registered', async () => {
    // Override the beforeEach mock with the single-spec catalog.
    mockFetchOnce(ONLY_GUIDE)
    const { container } = render(<SpecPicker pinned={null} onChange={() => {}} />)
    // Wait long enough for the fetch effect to settle, then assert the
    // picker hasn't rendered anything. (No role="group" present.)
    await waitFor(() => {
      expect(screen.queryByRole('group', { name: 'Backend' })).toBeNull()
    })
    expect(container.querySelector('.spec-picker')).toBeNull()
  })

  it('survives fetch failure (renders the loading placeholder, no crash)', async () => {
    ;(globalThis as { fetch?: unknown }).fetch = vi.fn()
      .mockRejectedValue(new Error('network down'))
    const { container } = render(<SpecPicker pinned={null} onChange={() => {}} />)
    // Should not throw, should not render the picker (specs.length < 2
    // gate hides it after loading fails).
    await waitFor(() => {
      expect(container.querySelector('.spec-picker')).toBeNull()
    })
  })
})
