const ANSI_FG = {
  30: 'var(--ink-gray-5)',
  31: 'var(--ink-red-7)',
  32: 'var(--ink-green-7)',
  33: 'var(--ink-amber-7)',
  34: 'var(--ink-blue-7)',
  35: 'var(--ink-purple-7)',
  36: 'var(--ink-cyan-7)',
  37: 'var(--ink-gray-8)',
  // Bright maps to a stronger ink step, not a lighter one.
  90: 'var(--ink-gray-5)',
  91: 'var(--ink-red-8)',
  92: 'var(--ink-green-8)',
  93: 'var(--ink-amber-8)',
  94: 'var(--ink-blue-8)',
  95: 'var(--ink-purple-8)',
  96: 'var(--ink-cyan-8)',
  97: 'var(--ink-gray-9)',
}

export function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function ansiToHtml(text) {
  let html = ''
  let openSpans = 0
  for (const part of text.split(/(\x1b\[[0-9;]*[A-Za-z])/)) {
    if (part.startsWith('\x1b[') && part.endsWith('m')) {
      for (const code of part.slice(2, -1).split(';')) {
        if (code === '0' || code === '') {
          html += '</span>'.repeat(openSpans)
          openSpans = 0
        } else if (code === '1') {
          html += '<span style="font-weight:bold">'
          openSpans++
        } else if (ANSI_FG[code]) {
          html += `<span style="color:${ANSI_FG[code]}">`
          openSpans++
        }
      }
    } else if (!part.startsWith('\x1b[')) {
      html += escapeHtml(part)
    }
  }
  return html + '</span>'.repeat(openSpans)
}

// Resolve \r (progress-bar overwrites): keep the last non-whitespace segment
function applyCarriageReturns(raw) {
  const parts = raw.split('\r')
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i].trim()) return parts[i].trimEnd()
  }
  return ''
}

export function processLine(raw) {
  return ansiToHtml(applyCarriageReturns(raw))
}
