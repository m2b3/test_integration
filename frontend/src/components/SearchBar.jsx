const SOURCE_OPTIONS = [
  { id: 'all', label: 'All' },
  { id: 'arxiv', label: 'arXiv' },
  { id: 'pubmed', label: 'PubMed' },
  { id: 'openreview', label: 'OpenReview' },
]

function SearchBar({
  keywordQuery,
  onKeywordQueryChange,
  onSemanticQueryChange,
  onSearch,
  onSourceChange,
  onTagAdd,
  onTagRemove,
  searchMode,
  selectedTags,
  semanticQuery,
  source,
}) {
  function handleSubmit(event) {
    event.preventDefault()
    onSearch()
  }

  function handleTagKeyDown(event) {
    if (event.key !== 'Enter') {
      return
    }
    event.preventDefault()
    const value = event.currentTarget.value.trim()
    if (!value) {
      return
    }
    onTagAdd(value)
    event.currentTarget.value = ''
  }

  return (
    <form className="search-panel" aria-label="Article search" onSubmit={handleSubmit}>
      <div className="search-grid">
        <label className="field">
          <span>Semantic</span>
          <input
            onChange={(event) => onSemanticQueryChange(event.target.value)}
            placeholder="Describe a research interest, e.g. climate effects on public health"
            type="search"
            value={semanticQuery}
          />
        </label>

        <label className="field">
          <span>Keyword</span>
          <input
            onChange={(event) => onKeywordQueryChange(event.target.value)}
            placeholder="Exact words, methods, authors, e.g. CRISPR drought tolerance"
            type="search"
            value={keywordQuery}
          />
        </label>

        <label className="field tag-filter-field">
          <span>Category tag</span>
          <input
            onKeyDown={handleTagKeyDown}
            placeholder="Press Enter to add, e.g. math.NT"
            type="text"
          />
        </label>
      </div>

      {selectedTags.length > 0 && (
        <div className="selected-tags" aria-label="Selected article tags">
          {selectedTags.map((tag) => (
            <button key={tag} type="button" onClick={() => onTagRemove(tag)}>
              {tag}
              <span aria-hidden="true">x</span>
            </button>
          ))}
        </div>
      )}

      <div className="search-footer">
        <div className="source-control" aria-label="Source filter">
          {SOURCE_OPTIONS.map((option) => (
            <button
              className={source === option.id ? 'is-active' : ''}
              key={option.id}
              type="button"
              onClick={() => onSourceChange(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="search-actions">
          <span className="search-mode">Mode: {searchMode}</span>
          <button className="primary-button compact-button" type="submit">
            Search
          </button>
        </div>
      </div>
    </form>
  )
}

export default SearchBar
