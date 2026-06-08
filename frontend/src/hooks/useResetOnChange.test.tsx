/**
 * Tests for useResetOnChange — the primitive App uses to clear the
 * in-flight annotation (image + framing note attached by the
 * "Chat about this figure" SplitButton) when the focused entity
 * changes. See useResetOnChange.ts for the regression context
 * (2026-06-07, thr_806a2ced).
 */
import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useResetOnChange } from './useResetOnChange'

describe('useResetOnChange', () => {
  it('does NOT fire on initial mount', () => {
    const onChange = vi.fn()
    renderHook(({ v }) => useResetOnChange(v, onChange), {
      initialProps: { v: 'workspace' },
    })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('fires when the watched value changes', () => {
    const onChange = vi.fn()
    const { rerender } = renderHook(({ v }) => useResetOnChange(v, onChange), {
      initialProps: { v: 'workspace' },
    })
    rerender({ v: 'res_46b57683' })
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('does NOT fire on re-render with the SAME value', () => {
    const onChange = vi.fn()
    const { rerender } = renderHook(({ v }) => useResetOnChange(v, onChange), {
      initialProps: { v: 'res_x' },
    })
    rerender({ v: 'res_x' })
    rerender({ v: 'res_x' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('fires once per change (not per render)', () => {
    const onChange = vi.fn()
    const { rerender } = renderHook(({ v }) => useResetOnChange(v, onChange), {
      initialProps: { v: 'a' },
    })
    rerender({ v: 'b' })
    rerender({ v: 'b' })   // same value, second render: should NOT fire
    rerender({ v: 'c' })
    rerender({ v: 'c' })   // same value, second render: should NOT fire
    expect(onChange).toHaveBeenCalledTimes(2)
  })

  it('handles falsy values correctly (e.g. null → "res_x")', () => {
    const onChange = vi.fn()
    const { rerender } = renderHook(({ v }) => useResetOnChange(v, onChange), {
      initialProps: { v: null as string | null },
    })
    rerender({ v: 'res_x' })
    expect(onChange).toHaveBeenCalledTimes(1)
    rerender({ v: null })
    expect(onChange).toHaveBeenCalledTimes(2)
  })
})
