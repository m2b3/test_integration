import ArticleCard from './ArticleCard'

function RecentlyViewedPage({ articles, onArticleOpen, onBack }) {
  return (
    <section className="profile-page" aria-label="Recently viewed articles">
      <button className="text-button" type="button" onClick={onBack}>
        Back to profile
      </button>

      <div className="page-heading">
        <h2>Recently viewed</h2>
        <p>{articles.length > 0 ? `Top ${Math.min(articles.length, 20)} articles` : 'You have no recent view history.'}</p>
      </div>

      <div className="article-list">
        {articles.slice(0, 20).map((article) => (
          <ArticleCard key={article.paper_key || article.id} article={article} onView={onArticleOpen} />
        ))}

        {articles.length === 0 && (
          <div className="empty-state">
            <h2>You have no recent view history</h2>
            <p>Open a paper from the feed and it will appear here.</p>
          </div>
        )}
      </div>
    </section>
  )
}

export default RecentlyViewedPage
