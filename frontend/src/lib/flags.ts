// Feature flags (frontend mirror of backend gates).
//
// Advisors (skeptic / methodologist / explorer / stylist) are PAUSED pending
// refinement — their suggestions aren't useful enough yet, and they cost cycles
// (10s polling + on-focus LLM calls). The components and backend code remain
// intact; this flag just gates the UI + polling off. The backend has its own
// guard (advisors.runner.advisors_enabled / ABA_ADVISORS_ENABLED). Re-enable
// both when we come back to refine them.
export const ADVISORS_ENABLED = false
