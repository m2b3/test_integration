const SOURCE_OPTIONS = [
  { id: 'all', label: 'All' },
  { id: 'arxiv', label: 'arXiv' },
  { id: 'pubmed', label: 'PubMed' },
]

function SearchBar({
  keywordQuery,
  onKeywordQueryChange,
  onSemanticQueryChange,
  onSearch,
  onSourceChange,
  searchMode,
  semanticQuery,
  source,
}) {
  function handleSubmit(event) {
    event.preventDefault()
    onSearch()
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
      </div>

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
