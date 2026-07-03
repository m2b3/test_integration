import ArticleCard from './ArticleCard'

function RecentlyViewedPage({ articles, onBack }) {
  return (
    <section className="profile-page" aria-label="Recently viewed articles">
      <button className="text-button" type="button" onClick={onBack}>
        Back to profile
      </button>

      <div className="page-heading">
        <h2>Recently viewed</h2>
        <p>Top {Math.min(articles.length, 20)} articles</p>
      </div>

      <div className="article-list">
        {articles.slice(0, 20).map((article) => (
          <ArticleCard key={article.paper_key || article.id} article={article} />
        ))}

        {articles.length === 0 && (
          <div className="empty-state">
            <h2>No articles yet</h2>
          </div>
        )}
      </div>
    </section>
  )
}

export default RecentlyViewedPage
