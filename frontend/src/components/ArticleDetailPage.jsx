import { useEffect, useRef } from 'react'
import { normalizeAuthors } from '../utils/articleFormat'

function formatSource(source) {
  return source
    .split('-')
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
}

function MathText({ children }) {
  const ref = useRef(null)

  useEffect(() => {
    if (window.MathJax?.typesetPromise && ref.current) {
      window.MathJax.typesetPromise([ref.current]).catch((error) => {
        console.error(error)
      })
    }
  }, [children])

  return (
    <div className="math-text" ref={ref}>
      {children}
    </div>
  )
}

function ArticleDetailPage({ article, onBack, onTagClick }) {
  const authors = normalizeAuthors(article.authors).join(', ')
  const articleUrl = article.url || ''
  const abstract = article.abstract || 'Abstract placeholder text for this prototype.'
  const articleId = article.paper_key || article.id || article.external_id || ''

  return (
    <section className="paper-page" aria-label="Paper detail">
      <button className="text-button" type="button" onClick={onBack}>
        Back
      </button>

      <div className="paper-shell">
        <div className="article-meta">
          <span>{formatSource(article.source)}</span>
          {articleId && <span className="paper-id">[{articleId}]</span>}
          <span>{article.published_date}</span>
        </div>

        <h2>{article.title}</h2>
        <p className="authors">{authors}</p>

        <section className="paper-section">
          <h3>Abstract</h3>
          <MathText>{abstract}</MathText>
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
            <button
              className="tag-chip tag-button"
              key={tag}
              type="button"
              onClick={() => onTagClick?.(tag)}
            >
              {tag}
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}

export default ArticleDetailPage
