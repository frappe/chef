export function fmtBytes(bytes) {
  const n = Number(bytes)
  if (!n || n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function fmtDateTime(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// Bake lifecycle -------------------------------------------------------------

export const ACTIVE_BAKE_STATES = new Set([
  'queued',
  'acquiring',
  'building',
  'verifying',
  'snapshotting',
  'publishing',
])

export function isBakeActive(status) {
  return ACTIVE_BAKE_STATES.has(status)
}

const BAKE_THEME = {
  queued: 'gray',
  acquiring: 'blue',
  building: 'blue',
  verifying: 'blue',
  snapshotting: 'blue',
  publishing: 'blue',
  succeeded: 'green',
  failed: 'red',
  aborted: 'orange',
}

export function bakeTheme(status) {
  return BAKE_THEME[status] || 'gray'
}

// Steps ----------------------------------------------------------------------

const STEP_THEME = {
  running: 'blue',
  changed: 'green',
  no_change: 'gray',
  failed: 'red',
}

export function stepTheme(state) {
  return STEP_THEME[state] || 'gray'
}

const STEP_LABEL = {
  running: 'Running',
  changed: 'Changed',
  no_change: 'No change',
  failed: 'Failed',
}

export function stepLabel(state) {
  return STEP_LABEL[state] || state
}

const STEP_ICON = {
  running: 'lucide-loader',
  changed: 'lucide-check',
  no_change: 'lucide-minus',
  failed: 'lucide-x',
}

export function stepIcon(state) {
  return STEP_ICON[state] || 'lucide-circle'
}

// Modes / snapshot kinds -----------------------------------------------------

export function modeTheme(mode) {
  if (mode === 'warm') return 'orange'
  if (mode === 'both') return 'purple'
  return 'blue'
}

export function kindTheme(kind) {
  return kind === 'warm' ? 'orange' : 'blue'
}
