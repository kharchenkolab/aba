/**
 * ExternalViewerActions — surfaces "open in an external viewer" launch buttons
 * (e.g. "↗ Explore in pagoda3") on an entity focus card.
 *
 * Self-gating: it runs the SAME client-side viewer dispatch the file tree uses
 * (viewers/dispatch) and renders nothing unless an *external*-mode viewer
 * matches the entity's artifact. So it's safe to drop into any focus view — a
 * dataset/result whose file is a .h5ad / .lstar.zarr gets a launch button;
 * everything else renders null. The AI fallbacks (ai-summary/ai-visualize) are
 * modal/canvas mode, so they're excluded by the mode filter.
 */
import type { Entity } from '../types'
import type { FileNode, ViewerInfo, ViewerRegistryEntry } from '../viewers/types'
import { useViewerRegistry, dispatchViewers } from '../viewers/dispatch'
import { launchExternal } from '../viewers/launch'

/** Build the dispatch node from an entity. No tree `path` is set, so
 *  launchExternal targets the entity (?entity=<id>) rather than a file path. */
export function entityToNode(entity: Entity): FileNode {
  const artifact = entity.artifact_path || ''
  const base = artifact ? (artifact.split('/').pop() || artifact) : (entity.title || '')
  const sizeRaw = entity.metadata?.size_bytes
  const size = typeof sizeRaw === 'number' ? sizeRaw : null
  return {
    kind: 'file',
    name: base,
    path: '',
    entity_id: entity.id,
    entity_type: entity.type,
    artifact_path: artifact || null,
    size,
  }
}

/** Pure gating decision: the dispatch node + the external-mode viewers that
 *  apply to this entity. Empty `viewers` → the component renders nothing. */
export function externalViewersFor(
  entity: Entity, registry: ViewerRegistryEntry[],
): { node: FileNode; viewers: ViewerInfo[] } {
  const node = entityToNode(entity)
  const viewers = dispatchViewers(node, registry).filter(v => v.mode === 'external')
  return { node, viewers }
}

export default function ExternalViewerActions({ entity, className }: { entity: Entity; className?: string }) {
  const registry = useViewerRegistry()
  if (!registry) return null
  const { node, viewers: externals } = externalViewersFor(entity, registry)
  if (externals.length === 0) return null
  return (
    <div className={className ?? 'focus__viewer-actions'}>
      {externals.map(v => (
        <button key={v.id} className="focus__promote" title="Opens in a new tab"
                onClick={() => launchExternal(node, v)}>
          ↗ {v.label}
        </button>
      ))}
      {/* Pack the prepared store into one STORED .lstar.zarr.zip (re-openable in
          pagoda3 / lstar). Reuses the viewer's cached store — instant after a view. */}
      <button className="focus__promote" title="Download as a single .lstar.zarr.zip (opens a new tab)"
              onClick={() => launchExternal(node, externals[0], { action: 'download' })}>
        ⬇ Download .lstar.zarr.zip
      </button>
    </div>
  )
}
