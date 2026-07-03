import { useEffect, useState } from 'react'
import { getArticles, getUserFeed } from './api/articles'
import { login, updateUserTags } from './api/users'
import ArticleCard from './components/ArticleCard'
import ManageInterestsPage from './components/ManageInterestsPage'
import ProfileModal from './components/ProfileModal'
import ProfilePage from './components/ProfilePage'
import RecentlyViewedPage from './components/RecentlyViewedPage'
import SearchBar from './components/SearchBar'
import './App.css'

const PROFILE_STORAGE_KEY = 'scicommons.profile'
const RECENTLY_VIEWED_STORAGE_KEY = 'scicommons.recentlyViewed'

function readStoredProfile() {
  try {
    const value = window.localStorage.getItem(PROFILE_STORAGE_KEY)
    return value ? JSON.parse(value) : null
  } catch {
    return null
  }
}

function readRecentlyViewed() {
  try {
    const value = window.localStorage.getItem(RECENTLY_VIEWED_STORAGE_KEY)
    return value ? JSON.parse(value) : []
  } catch {
    return []
  }
}

function App() {
  const [articles, setArticles] = useState([])
  const [recentlyViewed, setRecentlyViewed] = useState(() => readRecentlyViewed())
  const [isLoading, setIsLoading] = useState(true)
  const [semanticQuery, setSemanticQuery] = useState('')
  const [keywordQuery, setKeywordQuery] = useState('')
  const [source, setSource] = useState('all')
  const [profile, setProfile] = useState(() => readStoredProfile())
  const [activeFeed, setActiveFeed] = useState(() => (readStoredProfile() ? 'recommended' : 'all'))
  const [activePage, setActivePage] = useState('feed')
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
    const profileWithTags = {
      ...savedProfile,
      tags: nextProfile.tags,
      authors: nextProfile.authors,
    }

    await updateUserTags(profileWithTags.user_id, nextProfile.tags)

    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profileWithTags))
    setProfile(profileWithTags)
    setActiveFeed('recommended')
    setIsProfileOpen(false)
  }

  async function handleInterestSave(nextProfile) {
    await updateUserTags(nextProfile.user_id, nextProfile.tags)
    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(nextProfile))
    setProfile(nextProfile)
    setActivePage('profile')
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

  function handleArticleViewed(article) {
    const articleKey = article.paper_key || article.id
    const nextRecentlyViewed = [
      article,
      ...recentlyViewed.filter((item) => (item.paper_key || item.id) !== articleKey),
    ].slice(0, 20)

    setRecentlyViewed(nextRecentlyViewed)
    window.localStorage.setItem(RECENTLY_VIEWED_STORAGE_KEY, JSON.stringify(nextRecentlyViewed))
  }

  function renderContent() {
    if (activePage === 'profile' && profile) {
      return (
        <ProfilePage
          onBack={() => setActivePage('feed')}
          onManageInterests={() => setActivePage('manage-interests')}
          onRecentlyViewed={() => setActivePage('recently-viewed')}
          profile={profile}
        />
      )
    }

    if (activePage === 'recently-viewed' && profile) {
      return (
        <RecentlyViewedPage
          articles={recentlyViewed}
          onBack={() => setActivePage('profile')}
        />
      )
    }

    if (activePage === 'manage-interests' && profile) {
      return (
        <ManageInterestsPage
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
              onView={handleArticleViewed}
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
