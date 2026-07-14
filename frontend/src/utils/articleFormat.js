export function normalizeAuthors(authors) {
  if (Array.isArray(authors)) {
    return authors.map(String).filter(Boolean)
  }

  const text = String(authors || '').trim()
  if (!text) {
    return []
  }

  try {
    const parsed = JSON.parse(text)
    if (Array.isArray(parsed)) {
      return parsed.map(String).filter(Boolean)
    }
  } catch {
    // Fall through to separator-based parsing.
  }

  return text
    .split(/\s*,\s*/)
    .map((author) => author.trim().replace(/^["']|["']$/g, ''))
    .filter(Boolean)
}

export function truncateText(text, maxLength = 150) {
  if (text.length <= maxLength) {
    return text
  }
  const sliced = text.slice(0, maxLength).trimEnd()
  const lastSeparator = Math.max(sliced.lastIndexOf(', '), sliced.lastIndexOf(' '))
  return `${sliced.slice(0, lastSeparator > 80 ? lastSeparator : maxLength).trimEnd()}...`
}
