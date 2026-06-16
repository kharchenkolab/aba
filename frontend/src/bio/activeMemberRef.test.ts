/**
 * Tests for the multi-member viewport-pick ref shared between
 * ResultView (writer) and useChat (reader at send time).
 *
 * The module is intentionally minimal — global state with a result-id
 * gate. These tests pin the gate behaviour so a stale pick can't leak
 * across navigations into the next chat send.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { setActiveMember, getActiveMember, clearActiveMember } from './activeMemberRef'

describe('activeMemberRef', () => {
  beforeEach(() => { clearActiveMember() })

  it('round-trips a member id for its own result', () => {
    setActiveMember('res_a', 'm_one')
    expect(getActiveMember('res_a')).toBe('m_one')
  })

  it('returns null when asked about a different result', () => {
    // The whole point: useChat reads with the current focusEntityId. If
    // the user navigated, the recorded pick belongs to the PRIOR result
    // and must not leak into the new send.
    setActiveMember('res_a', 'm_one')
    expect(getActiveMember('res_b')).toBeNull()
  })

  it('returns null when nothing has been recorded', () => {
    expect(getActiveMember('res_a')).toBeNull()
  })

  it('setActiveMember(result, null) means "focused but no pick"', () => {
    setActiveMember('res_a', null)
    expect(getActiveMember('res_a')).toBeNull()
  })

  it('the last write wins across results', () => {
    setActiveMember('res_a', 'm_one')
    setActiveMember('res_b', 'm_two')
    expect(getActiveMember('res_a')).toBeNull()
    expect(getActiveMember('res_b')).toBe('m_two')
  })

  it('clearActiveMember() drops everything', () => {
    setActiveMember('res_a', 'm_one')
    clearActiveMember()
    expect(getActiveMember('res_a')).toBeNull()
  })

  it('clearActiveMember(forResultId) only drops the matching pick', () => {
    // Idempotency: ResultView calls clear on unmount, but another
    // ResultView for a different result may have just written its
    // own pick. The targeted clear must NOT wipe the new owner.
    setActiveMember('res_b', 'm_two')
    clearActiveMember('res_a')               // mismatched id
    expect(getActiveMember('res_b')).toBe('m_two')
    clearActiveMember('res_b')               // matched id
    expect(getActiveMember('res_b')).toBeNull()
  })

  it('empty result-id input is rejected by both setter and getter', () => {
    setActiveMember('', 'm_one')             // no-op
    expect(getActiveMember('')).toBeNull()
    setActiveMember('res_a', 'm_one')
    expect(getActiveMember('')).toBeNull()   // gate still blocks
  })
})
