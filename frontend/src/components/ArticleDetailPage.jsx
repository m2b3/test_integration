function formatSource(source) {
  return source
    .split('-')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function ArticleDetailPage({ article, onBack }) {
  const authors = Array.isArray(article.authors) ? article.authors.join(', ') : article.authors
  const articleUrl = article.url || ''
  const abstract = article.abstract || 'Abstract placeholder text for this prototype.'

  return (
    <section className="paper-page" aria-label="Paper detail">
      <button className="text-button" type="button" onClick={onBack}>
        Back
      </button>

      <div className="paper-shell">
        <div className="article-meta">
          <span>{formatSource(article.source)}</span>
          <span>{article.published_date}</span>
        </div>

        <h2>{article.title}</h2>
        <p className="authors">{authors}</p>

        <section className="paper-section">
          <h3>Abstract</h3>
          <p>{abstract}</p>
        </section>

        <section className="paper-section">
          <h3>Original paper</h3>
          {articleUrl ? (
            <a href={articleUrl} target="_blank" rel="noreferrer">
              {articleUrl}
            </a>
          ) : (
            <p className="placeholder-text">Original URL placeholder</p>
          )}
        </section>

        <div className="tag-row" aria-label="Article tags">
          {(article.tags || []).map((tag) => (
            <span className="tag-chip" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}

export default ArticleDetailPage
