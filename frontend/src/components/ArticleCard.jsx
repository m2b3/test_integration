import { normalizeAuthors, truncateText } from '../utils/articleFormat'
import MathText from './MathText'

function formatSource(source) {
  return source
    .split('-')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function formatArticleId(article) {
  return article.paper_key || article.id || article.external_id || ''
}

function ArticleCard({ article, onTagClick, onView }) {
  const authors = truncateText(normalizeAuthors(article.authors).join(', '))
  const articleId = formatArticleId(article)

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
        {articleId && <span className="paper-id">[{articleId}]</span>}
        <span>{article.published_date}</span>
      </div>
      <h2>
        <MathText>{article.title}</MathText>
      </h2>
      <p className="authors">{authors}</p>
      <div className="tag-row" aria-label="Article tags">
        {(article.tags || []).map((tag) => (
          <button
            className="tag-chip tag-button"
            key={tag}
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              onTagClick?.(tag)
            }}
          >
            {tag}
          </button>
        ))}
      </div>
    </article>
  )
}

export default ArticleCard
