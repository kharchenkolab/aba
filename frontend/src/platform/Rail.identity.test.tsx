/**
 * The account button must never show a hardcoded person.
 *
 * WHAT THIS GUARDS. Rail.tsx rendered the literals "Peter" and "PP", so every
 * user of a shared deployment saw the deployer's name as their own account
 * (found 2026-08-28 on the VBC pilot). It looked like everyone was logged in as
 * one person. Nothing exposed the real identity, so there was nothing to read —
 * /api/health now reports the user the server runs as.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { displayName, initialsFor } from './Rail'

const SRC = readFileSync(join(__dirname, 'Rail.tsx'), 'utf8')

describe('account identity is derived, never hardcoded', () => {
  it('renders no hardcoded person in the button', () => {
    // the JSX only — comments explain the history and legitimately say the names
    const code = SRC.split('\n')
      .filter(l => !l.trim().startsWith('//') && !l.trim().startsWith('*'))
      .join('\n')
    expect(code).not.toMatch(/>\s*Peter\s*</)
    expect(code).not.toMatch(/>\s*PP\s*</)
  })

  it('reads the identity from the server', () => {
    expect(SRC).toMatch(/\/api\/health/)
  })

  it('derives a first name from a username', () => {
    expect(displayName('peter.kharchenko')).toBe('Peter')
    expect(displayName('anna-schmidt')).toBe('Anna')
    expect(displayName('pkharchenko')).toBe('Pkharchenko')
  })

  it('derives initials, and does NOT double a single token', () => {
    // "PP" was a doubled single initial; one token must give one letter.
    expect(initialsFor('peter.kharchenko')).toBe('PK')
    expect(initialsFor('anna_schmidt')).toBe('AS')
    expect(initialsFor('pkharchenko')).toBe('P')
  })

  it('falls back to something neutral, never to a name', () => {
    // A failed fetch must not leave another human's name on screen.
    expect(SRC).toMatch(/name:\s*'Account'/)
    expect(initialsFor('')).toBe('—')
    expect(displayName('')).toBe('Account')
  })
})

describe('the suggestion badge stays hidden until it can be acted on', () => {
  it('does not render a count that cannot be cleared', () => {
    const code = SRC.split('\n')
      .filter(l => !l.trim().startsWith('//') && !l.trim().startsWith('*'))
      .join('\n')
    expect(code).not.toMatch(/rail__badge/)
    expect(code).not.toMatch(/context-suggestions/)
  })
})
