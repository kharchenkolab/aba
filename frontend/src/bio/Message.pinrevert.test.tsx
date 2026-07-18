/**
 * FigurePin failure reconciliation: a pin POST that fails must REVERT the
 * optimistic red — otherwise the glyph shows pinned with no Result behind
 * it, and the next click routes to unpin against nothing (the discordant
 * state class from the live study, pin direction).
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { FigurePin } from './Message'
import type { Entity } from '../types'

const fig = { id: 'fig_1', type: 'figure', title: 'curve' } as unknown as Entity

describe('FigurePin failure revert', () => {
  it('reverts the optimistic pinned state when onPin reports failure', async () => {
    const onPin = vi.fn().mockResolvedValue(false)      // server said no
    render(<FigurePin entity={fig} isPinned={false} onPin={onPin} />)
    const btn = screen.getByTitle('Pin this figure')
    await act(async () => { btn.click() })
    expect(onPin).toHaveBeenCalledWith('fig_1', true)
    expect(screen.getByTitle('Pin this figure')).toBeTruthy()   // back to unpinned
  })

  it('keeps the pinned state when onPin succeeds', async () => {
    const onPin = vi.fn().mockResolvedValue(true)
    render(<FigurePin entity={fig} isPinned={false} onPin={onPin} />)
    await act(async () => { screen.getByTitle('Pin this figure').click() })
    expect(screen.getByTitle('Pinned — click to unpin')).toBeTruthy()
  })
})
