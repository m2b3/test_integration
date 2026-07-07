import { useEffect, useState } from 'react'
import { getArticles, getUserFeed } from './api/articles'
import { addRecentlyViewed, getRecentlyViewed, getUserProfile, login, updateUserProfile } from './api/users'
import ArticleCard from './components/ArticleCard'
import ArticleDetailPage from './components/ArticleDetailPage'
import ManageInterestsPage from './components/ManageInterestsPage'
import ProfileModal from './components/ProfileModal'
import ProfilePage from './components/ProfilePage'
import RecentlyViewedPage from './components/RecentlyViewedPage'
import SearchBar from './components/SearchBar'
import './App.css'

const PROFILE_STORAGE_KEY = 'scicommons.profile'

function readStoredProfile() {
  try {
    const value = window.localStorage.getItem(PROFILE_STORAGE_KEY)
    return value ? JSON.parse(value) : null
  } catch {
    return null
  }
}

function App() {
  const [articles, setArticles] = useState([])
  const [recentlyViewed, setRecentlyViewed] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [semanticQuery, setSemanticQuery] = useState('')
  const [keywordQuery, setKeywordQuery] = useState('')
  const [source, setSource] = useState('all')
  const [profile, setProfile] = useState(() => readStoredProfile())
  const [activeFeed, setActiveFeed] = useState(() => (readStoredProfile() ? 'recommended' : 'all'))
  const [activePage, setActivePage] = useState('feed')
  const [selectedArticle, setSelectedArticle] = useState(null)
  const [isProfileOpen, setIsProfileOpen] = useState(false)
  const profileName = profile?.username || profile?.email || 'User'

  const searchMode =
    semanticQuery.trim() && keywordQuery.trim()
      ? 'hybrid'
      : semanticQuery.trim()
        ? 'semantic'
        : keywordQuery.trim()
          ? 'keyword'
          : 'none'

  useEffect(() => {
    let isActive = true

    async function loadArticles() {
      setIsLoading(true)
      const filters = {
        semantic_query: semanticQuery,
        keyword_query: keywordQuery,
        search_mode: searchMode,
        source,
      }
      const nextArticles =
        activeFeed === 'recommended' && profile
          ? await getUserFeed(profile.user_id, filters)
          : await getArticles(filters)

      if (isActive) {
        setArticles(nextArticles)
        setIsLoading(false)
      }
    }

    loadArticles()

    return () => {
      isActive = false
    }
  }, [activeFeed, keywordQuery, profile, searchMode, semanticQuery, source])

  async function handleProfileSave(nextProfile) {
    const savedProfile = await login(nextProfile)
    const profileWithTags = await updateUserProfile(savedProfile.user_id, {
      ...savedProfile,
      tags: nextProfile.tags,
      authors: nextProfile.authors,
    })

    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profileWithTags))
    setProfile(profileWithTags)
    setActiveFeed('recommended')
    setIsProfileOpen(false)
  }

  async function handleInterestSave(nextProfile) {
    const savedProfile = await updateUserProfile(nextProfile.user_id, nextProfile)
    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(savedProfile))
    setProfile(savedProfile)
    setActivePage('profile')
  }

  async function handleManageInterestsPage() {
    if (!profile) {
      setIsProfileOpen(true)
      return
    }

    const latestProfile = await getUserProfile(profile.user_id)
    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(latestProfile))
    setProfile(latestProfile)
    setActivePage('manage-interests')
  }

  function handleFeedChange(nextFeed) {
    if (nextFeed === 'recommended' && !profile) {
      setIsProfileOpen(true)
      return
    }
    setActiveFeed(nextFeed)
  }

  function handleProfileClick() {
    if (!profile) {
      setIsProfileOpen(true)
      return
    }
    setActivePage('profile')
  }

  async function handleRecentlyViewedPage() {
    if (!profile) {
      setIsProfileOpen(true)
      return
    }
    const nextRecentlyViewed = await getRecentlyViewed(profile.user_id, 20)
    setRecentlyViewed(nextRecentlyViewed)
    setActivePage('recently-viewed')
  }

  async function handleArticleOpen(article) {
    setSelectedArticle(article)
    setActivePage('paper')

    if (profile) {
      const viewedArticle = await addRecentlyViewed(profile.user_id, article)
      setRecentlyViewed((currentArticles) => [
        viewedArticle,
        ...currentArticles.filter((item) => (item.paper_key || item.id) !== viewedArticle.paper_key),
      ].slice(0, 20))
    }
  }

  function renderContent() {
    if (activePage === 'paper' && selectedArticle) {
      return (
        <ArticleDetailPage
          article={selectedArticle}
          onBack={() => setActivePage('feed')}
        />
      )
    }

    if (activePage === 'profile' && profile) {
      return (
        <ProfilePage
          onBack={() => setActivePage('feed')}
          onManageInterests={handleManageInterestsPage}
          onRecentlyViewed={handleRecentlyViewedPage}
          profile={profile}
        />
      )
    }

    if (activePage === 'recently-viewed' && profile) {
      return (
        <RecentlyViewedPage
          articles={recentlyViewed}
          onArticleOpen={handleArticleOpen}
          onBack={() => setActivePage('profile')}
        />
      )
    }

    if (activePage === 'manage-interests' && profile) {
      return (
        <ManageInterestsPage
          key={`${profile.user_id}-${(profile.tags || []).join('|')}-${(profile.authors || []).join('|')}`}
          onBack={() => setActivePage('profile')}
          onSave={handleInterestSave}
          profile={profile}
        />
      )
    }

    return (
      <section className="feed-layout" aria-label="Article feed">
        <div className="feed-tabs" aria-label="Feed type">
          <button
            className={activeFeed === 'recommended' ? 'is-active' : ''}
            type="button"
            onClick={() => handleFeedChange('recommended')}
          >
            Recommended
            {!profile && <span className="locked-label">Locked</span>}
          </button>
          <button
            className={activeFeed === 'all' ? 'is-active' : ''}
            type="button"
            onClick={() => handleFeedChange('all')}
          >
            All Feed
          </button>
        </div>

        <SearchBar
          keywordQuery={keywordQuery}
          onKeywordQueryChange={setKeywordQuery}
          onSemanticQueryChange={setSemanticQuery}
          onSourceChange={setSource}
          searchMode={searchMode}
          semanticQuery={semanticQuery}
          source={source}
        />

        <div className="feed-summary">
          <span>{isLoading ? 'Loading articles' : `${articles.length} articles`}</span>
          <span>
            {activeFeed === 'recommended' ? 'Recommended feed' : "Yesterday's all feed"}
          </span>
        </div>

        <div className="article-list">
          {articles.map((article) => (
            <ArticleCard
              key={article.paper_key}
              article={article}
              onView={handleArticleOpen}
            />
          ))}

          {!isLoading && articles.length === 0 && (
            <div className="empty-state">
              <h2>No matching articles</h2>
              <p>Try changing the search terms or source filter.</p>
            </div>
          )}
        </div>
      </section>
    )
  }

  return (
    <main className="app-shell">
      <header className="topbar compact">
        <div>
          <h1>Scicommons</h1>
          <p>Daily academic articles</p>
        </div>
        {profile ? (
          <button className="avatar-button" type="button" onClick={handleProfileClick}>
            <span className="avatar">{profileName.slice(0, 1).toUpperCase()}</span>
          </button>
        ) : (
          <button className="profile-button" type="button" onClick={handleProfileClick}>
            Log in
          </button>
        )}
      </header>

      {renderContent()}

      {isProfileOpen && (
        <ProfileModal
          initialProfile={profile}
          onClose={() => setIsProfileOpen(false)}
          onSave={handleProfileSave}
        />
      )}
    </main>
  )
}

export default App
