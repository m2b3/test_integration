import { useEffect, useState } from 'react'
import { getArticles, getTags, getUserFeed } from './api/articles'
import { login, updateUserTags } from './api/users'
import ArticleCard from './components/ArticleCard'
import ProfileModal from './components/ProfileModal'
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
  const [allTags, setAllTags] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [semanticQuery, setSemanticQuery] = useState('')
  const [keywordQuery, setKeywordQuery] = useState('')
  const [source, setSource] = useState('all')
  const [profile, setProfile] = useState(() => readStoredProfile())
  const [activeFeed, setActiveFeed] = useState(() => (readStoredProfile() ? 'recommended' : 'all'))
  const [isProfileOpen, setIsProfileOpen] = useState(false)

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

    async function loadOptions() {
      const nextTags = await getTags()
      if (isActive) {
        setAllTags(nextTags.map((tag) => tag.id))
      }
    }

    loadOptions()

    return () => {
      isActive = false
    }
  }, [])

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

  function handleFeedChange(nextFeed) {
    if (nextFeed === 'recommended' && !profile) {
      setIsProfileOpen(true)
      return
    }
    setActiveFeed(nextFeed)
  }

  return (
    <main className="app-shell">
      <header className="topbar compact">
        <div>
          <h1>Scicommons</h1>
          <p>Daily academic articles</p>
        </div>
        <button className="profile-button" type="button" onClick={() => setIsProfileOpen(true)}>
          {profile?.username || 'Log in'}
        </button>
      </header>

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
            <ArticleCard key={article.paper_key} article={article} />
          ))}

          {!isLoading && articles.length === 0 && (
            <div className="empty-state">
              <h2>No matching articles</h2>
              <p>Try changing the search terms or source filter.</p>
            </div>
          )}
        </div>
      </section>

      {isProfileOpen && (
        <ProfileModal
          allTags={allTags}
          initialProfile={profile}
          onClose={() => setIsProfileOpen(false)}
          onSave={handleProfileSave}
        />
      )}
    </main>
  )
}

export default App
