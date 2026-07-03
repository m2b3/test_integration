const SOURCE_OPTIONS = [
  { id: 'all', label: 'All' },
  { id: 'arxiv', label: 'arXiv' },
  { id: 'pubmed', label: 'PubMed' },
]

function SearchBar({
  keywordQuery,
  onKeywordQueryChange,
  onSemanticQueryChange,
  onSourceChange,
  searchMode,
  semanticQuery,
  source,
}) {
  return (
    <section className="search-panel" aria-label="Article search">
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
        <span className="search-mode">Mode: {searchMode}</span>
      </div>
    </section>
  )
}

export default SearchBar
