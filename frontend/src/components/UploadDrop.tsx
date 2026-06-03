/**
 * UploadDrop — modal for adding data to the active project.
 *
 * UX:
 *  - One drop-zone accepts files OR folders (drag-and-drop).
 *  - Click-to-browse falls back to multi-file picker (OS file dialog doesn't
 *    support a mixed file/folder mode — folders require drag).
 *  - Each top-level dropped item becomes ONE dataset entity:
 *      file  → /api/upload         (one file-shaped dataset)
 *      dir   → /api/upload-folder  (one directory-shaped dataset, layout preserved)
 *  - Each in-flight upload shows its own pill with a live progress bar.
 *  - XHR (not fetch) so we can hook upload.onprogress.
 */
import { useRef, useState, useCallback, useEffect } from 'react'
import './UploadDrop.css'

type UploadItem = {
  id: string
  kind: 'file' | 'folder'
  name: string
  status: 'queued' | 'uploading' | 'done' | 'error'
  loaded: number
  total: number
  xhr?: XMLHttpRequest
  error?: string
}

type Walked =
  | { kind: 'file'; file: File }
  | { kind: 'folder'; name: string; files: { file: File; rel: string }[] }

interface Props {
  onClose: () => void
  onUploaded: () => void
}

const newId = () => 'u' + Math.random().toString(36).slice(2, 10)

// Walk a DataTransferEntry recursively. Returns one Walked per top-level
// entry (file → 1 file, folder → 1 folder with all descendant files).
async function readEntry(entry: FileSystemEntry | null): Promise<Walked | null> {
  if (!entry) return null
  if (entry.isFile) {
    const file = await new Promise<File>((res, rej) =>
      (entry as FileSystemFileEntry).file(res, rej))
    return { kind: 'file', file }
  }
  const dir = entry as FileSystemDirectoryEntry
  const files: { file: File; rel: string }[] = []
  await walkDir(dir, dir.name, files)
  return { kind: 'folder', name: dir.name, files }
}

async function walkDir(
  dir: FileSystemDirectoryEntry,
  basePath: string,
  out: { file: File; rel: string }[],
): Promise<void> {
  const reader = dir.createReader()
  while (true) {
    // readEntries returns ~100 at a time; loop until empty.
    const entries: FileSystemEntry[] = await new Promise((res, rej) =>
      reader.readEntries(res, rej))
    if (entries.length === 0) break
    for (const e of entries) {
      if (e.isFile) {
        const file = await new Promise<File>((res, rej) =>
          (e as FileSystemFileEntry).file(res, rej))
        const rel = `${basePath.slice(dir.name.length + 1)}${basePath.length > dir.name.length ? '/' : ''}${file.name}`
        // Strip the leading dir name; rel should be the in-bundle path.
        out.push({ file, rel: rel.replace(/^\/+/, '') || file.name })
      } else {
        await walkDir(e as FileSystemDirectoryEntry, `${basePath}/${e.name}`, out)
      }
    }
  }
}

export default function UploadDrop({ onClose, onUploaded }: Props) {
  const [items, setItems] = useState<UploadItem[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const updateItem = useCallback((id: string, patch: Partial<UploadItem>) => {
    setItems(prev => prev.map(it => (it.id === id ? { ...it, ...patch } : it)))
  }, [])

  // XHR wrapper that returns a Promise + reports progress via callback.
  const xhrUpload = useCallback((url: string, fd: FormData, id: string): XMLHttpRequest => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) updateItem(id, { loaded: e.loaded, total: e.total, status: 'uploading' })
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        updateItem(id, { status: 'done' })
        onUploaded()
      } else {
        let msg = `HTTP ${xhr.status}`
        try { const j = JSON.parse(xhr.responseText); if (j.detail) msg = j.detail } catch { /* ignore */ }
        updateItem(id, { status: 'error', error: msg })
      }
    }
    xhr.onerror = () => updateItem(id, { status: 'error', error: 'network error' })
    xhr.onabort = () => updateItem(id, { status: 'error', error: 'cancelled' })
    xhr.send(fd)
    return xhr
  }, [onUploaded, updateItem])

  const uploadOneFile = useCallback((file: File) => {
    const id = newId()
    const item: UploadItem = {
      id, kind: 'file', name: file.name, status: 'queued',
      loaded: 0, total: file.size,
    }
    setItems(prev => [...prev, item])
    const fd = new FormData()
    fd.append('file', file)
    const xhr = xhrUpload('/api/upload', fd, id)
    updateItem(id, { xhr })
  }, [xhrUpload, updateItem])

  const uploadFolder = useCallback((folderName: string, files: { file: File; rel: string }[]) => {
    if (files.length === 0) return     // empty folder — silently skip
    const id = newId()
    const total = files.reduce((n, f) => n + f.file.size, 0)
    const item: UploadItem = {
      id, kind: 'folder', name: folderName, status: 'queued',
      loaded: 0, total,
    }
    setItems(prev => [...prev, item])
    const fd = new FormData()
    fd.append('folder_name', folderName)
    files.forEach(f => {
      fd.append('files', f.file)
      fd.append('rel_paths', f.rel)
    })
    const xhr = xhrUpload('/api/upload-folder', fd, id)
    updateItem(id, { xhr })
  }, [xhrUpload, updateItem])

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    // dataTransfer.items is a live list — copy to array before async work,
    // it's invalid once the event handler returns.
    const items = Array.from(e.dataTransfer.items).filter(it => it.kind === 'file')
    const entries = items.map(it => it.webkitGetAsEntry())
    for (const entry of entries) {
      const walked = await readEntry(entry)
      if (!walked) continue
      if (walked.kind === 'file') uploadOneFile(walked.file)
      else uploadFolder(walked.name, walked.files)
    }
  }, [uploadFolder, uploadOneFile])

  const handleClickPicker = useCallback(() => inputRef.current?.click(), [])
  const handlePicked = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const fs = Array.from(e.target.files ?? [])
    fs.forEach(uploadOneFile)
    e.target.value = ''      // allow re-picking the same file
  }, [uploadOneFile])

  const inFlight = items.filter(i => i.status === 'uploading' || i.status === 'queued')
  const handleClose = useCallback(() => {
    if (inFlight.length > 0) {
      if (!window.confirm(`${inFlight.length} upload(s) still running. Cancel and close?`)) return
      inFlight.forEach(i => i.xhr?.abort())
    }
    onClose()
  }, [inFlight, onClose])

  // Auto-close when ALL uploads finished cleanly. Brief delay so the user
  // sees the final 100% / ✓ before the dialog dismisses. If any errored,
  // leave open so the user can read the error pill.
  useEffect(() => {
    if (items.length === 0) return
    const allDone = items.every(i => i.status === 'done')
    if (!allDone) return
    const t = setTimeout(onClose, 700)
    return () => clearTimeout(t)
  }, [items, onClose])

  return (
    <div className="updrop__backdrop" onClick={handleClose}>
      <div className="updrop__dialog" onClick={e => e.stopPropagation()}>
        <div className="updrop__header">
          <span className="updrop__title">Add data to this project</span>
          <button className="updrop__close" onClick={handleClose} title="Close">×</button>
        </div>

        <div
          className={`updrop__zone${dragging ? ' updrop__zone--active' : ''}`}
          onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
        >
          <div className="updrop__zone-main">Drop files or folders here</div>
          <div className="updrop__zone-sep">— or —</div>
          <button className="updrop__pick-btn" onClick={handleClickPicker}>click to pick files…</button>
          <div className="updrop__hint">(folders: drag-and-drop only — the OS picker can't mix modes)</div>
          <input ref={inputRef} type="file" multiple onChange={handlePicked} style={{ display: 'none' }} />
        </div>

        {items.length > 0 && (
          <div className="updrop__items">
            {items.map(it => <UploadPill key={it.id} item={it} />)}
          </div>
        )}
      </div>
    </div>
  )
}

function UploadPill({ item }: { item: UploadItem }) {
  const pct = item.total > 0 ? Math.min(100, Math.round((item.loaded / item.total) * 100)) : 0
  const sizeLabel = item.total > 0
    ? `${formatBytes(item.loaded)} / ${formatBytes(item.total)}`
    : ''
  const icon = item.status === 'done' ? '✓'
             : item.status === 'error' ? '✗'
             : item.kind === 'folder' ? '📁' : '📄'
  return (
    <div className={`updrop__item updrop__item--${item.status}`}>
      <span className="updrop__icon">{icon}</span>
      <span className="updrop__name" title={item.name}>{item.name}</span>
      <span className="updrop__bar"><span className="updrop__bar-fill" style={{ width: `${item.status === 'done' ? 100 : pct}%` }} /></span>
      <span className="updrop__pct">{item.status === 'error' ? (item.error || 'error') : `${pct}%`}</span>
      <span className="updrop__size">{sizeLabel}</span>
      {(item.status === 'uploading' || item.status === 'queued') && (
        <button className="updrop__cancel" onClick={() => item.xhr?.abort()} title="Cancel">×</button>
      )}
    </div>
  )
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}
