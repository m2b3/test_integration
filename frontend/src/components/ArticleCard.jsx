function formatSource(source) {
  return source
    .split('-')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function ArticleCard({ article, onView }) {
  const authors = Array.isArray(article.authors) ? article.authors.join(', ') : article.authors

  function handleKeyDown(event) {
    if (!onView) {
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onView(article)
    }
  }

  return (
    <article
      className={onView ? 'article-card is-clickable' : 'article-card'}
      onClick={onView ? () => onView(article) : undefined}
      onKeyDown={handleKeyDown}
      role={onView ? 'button' : undefined}
      tabIndex={onView ? 0 : undefined}
    >
      <div className="article-meta">
        <span>{formatSource(article.source)}</span>
        <span>{article.published_date}</span>
      </div>
      <h2>{article.title}</h2>
      <p className="authors">{authors}</p>
      <div className="tag-row" aria-label="Article tags">
        {article.tags.map((tag) => (
          <span className="tag-chip" key={tag}>
            {tag}
          </span>
        ))}
      </div>
    </article>
  )
}

export default ArticleCard
