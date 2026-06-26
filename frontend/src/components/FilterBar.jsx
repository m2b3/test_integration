import { useMemo, useState } from 'react'

function searchableTagText(tag) {
  return tag.replaceAll('-', ' ').toLowerCase()
}

function tagAcronym(tag) {
  return searchableTagText(tag)
    .split(/\s+/)
    .map((word) => word[0])
    .join('')
}

function tagMatchRank(tag, input) {
  const tagId = tag.toLowerCase()
  const tagText = searchableTagText(tag)
  const acronym = tagAcronym(tag)
  const words = tagText.split(/\s+/)

  if (tagId === input || tagText === input) {
    return 0
  }
  if (tagId.startsWith(input) || tagText.startsWith(input)) {
    return 1
  }
  if (words.some((word) => word.startsWith(input))) {
    return 2
  }
  if (acronym.startsWith(input)) {
    return 3
  }
  if (tagId.includes(input) || tagText.includes(input) || acronym.includes(input)) {
    return 4
  }
  return null
}

function FilterBar({
  allTags,
  matchMode,
  onMatchModeChange,
  onSearchChange,
  onSelectedTagsChange,
  onSourceChange,
  searchTerm,
  selectedTags,
  source,
  sources,
}) {
  const [tagInput, setTagInput] = useState('')

  const suggestions = useMemo(() => {
    const normalizedInput = tagInput.trim().toLowerCase()
    if (!normalizedInput) {
      return []
    }

    return allTags
      .filter((tag) => !selectedTags.includes(tag))
      .map((tag) => ({
        tag,
        rank: tagMatchRank(tag, normalizedInput),
      }))
      .filter((item) => item.rank !== null)
      .sort((a, b) => a.rank - b.rank || a.tag.localeCompare(b.tag))
      .map((item) => item.tag)
      .slice(0, 6)
  }, [allTags, selectedTags, tagInput])

  function addTag(tag) {
    if (!tag || selectedTags.includes(tag)) {
      return
    }
    onSelectedTagsChange([...selectedTags, tag])
    setTagInput('')
  }

  function removeTag(tagToRemove) {
    onSelectedTagsChange(selectedTags.filter((tag) => tag !== tagToRemove))
  }

  function handleTagKeyDown(event) {
    if (event.key === 'Enter') {
      event.preventDefault()
      const exactTag = allTags.find((tag) => tag === tagInput.trim().toLowerCase())
      addTag(exactTag || suggestions[0])
    }
  }

  return (
    <section className="filter-bar" aria-label="Article filters">
      <div className="filter-grid">
        <label className="field search-field">
          <span>Search</span>
          <input
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Title or author"
            type="search"
            value={searchTerm}
          />
        </label>

        <label className="field tag-field">
          <span>Tags</span>
          <input
            onChange={(event) => setTagInput(event.target.value)}
            onKeyDown={handleTagKeyDown}
            placeholder="biology, medicine..."
            type="text"
            value={tagInput}
          />
          {suggestions.length > 0 && (
            <div className="suggestions">
              {suggestions.map((tag) => (
                <button key={tag} type="button" onClick={() => addTag(tag)}>
                  {tag}
                </button>
              ))}
            </div>
          )}
        </label>

        <label className="field source-field">
          <span>Source</span>
          <select onChange={(event) => onSourceChange(event.target.value)} value={source}>
            <option value="all">All sources</option>
            {sources.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <div className="match-control" aria-label="Tag match mode">
          <button
            className={matchMode === 'or' ? 'is-active' : ''}
            type="button"
            onClick={() => onMatchModeChange('or')}
          >
            OR
          </button>
          <button
            className={matchMode === 'and' ? 'is-active' : ''}
            type="button"
            onClick={() => onMatchModeChange('and')}
          >
            AND
          </button>
        </div>
      </div>

      <div className="selected-tags">
        {selectedTags.map((tag) => (
          <button key={tag} type="button" onClick={() => removeTag(tag)}>
            {tag}
            <span aria-hidden="true">x</span>
          </button>
        ))}
        {selectedTags.length > 0 && (
          <button className="clear-button" type="button" onClick={() => onSelectedTagsChange([])}>
            Clear
          </button>
        )}
      </div>
    </section>
  )
}

export default FilterBar
