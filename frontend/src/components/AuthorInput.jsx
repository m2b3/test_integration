import { forwardRef, useImperativeHandle, useState } from 'react'

function normalize(value) {
  return value.trim().replace(/\s+/g, ' ')
}

function normalizeAuthors(authors) {
  const values = Array.isArray(authors) ? authors : String(authors || '').split(',')
  return values.map((author) => normalize(String(author))).filter(Boolean)
}

const AuthorInput = forwardRef(function AuthorInput({ authors, onChange }, ref) {
  const [inputValue, setInputValue] = useState('')

  function addAuthors(value) {
    const pendingAuthors = normalizeAuthors(value)
    if (pendingAuthors.length === 0) {
      return authors
    }

    const existing = new Set(authors.map((author) => author.toLowerCase()))
    const additions = pendingAuthors.filter((author) => !existing.has(author.toLowerCase()))
    if (additions.length === 0) {
      setInputValue('')
      return authors
    }

    const nextAuthors = [...authors, ...additions]
    onChange(nextAuthors)
    setInputValue('')
    return nextAuthors
  }

  function removeAuthor(value) {
    onChange(authors.filter((author) => author !== value))
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter') {
      event.preventDefault()
      addAuthors(inputValue)
    }
  }

  useImperativeHandle(ref, () => ({
    commitPending: () => addAuthors(inputValue),
  }))

  return (
    <div className="interest-input author-input">
      <label className="field optional-field">
        <span>Authors to follow optional</span>
        <input
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type an author and press Enter"
          type="text"
          value={inputValue}
        />
      </label>

      <div className="interest-tags author-tags" aria-label="Selected authors">
        {authors.map((author) => (
          <span className="interest-chip author-chip" key={author}>
            {author}
            <button type="button" aria-label={`Remove ${author}`} onClick={() => removeAuthor(author)}>
              x
            </button>
          </span>
        ))}
      </div>
    </div>
  )
})

export { normalizeAuthors }
export default AuthorInput
