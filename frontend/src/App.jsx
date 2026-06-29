import { useEffect, useState } from 'react'
import { getArticleSources, getArticles, getTags } from './api/articles'
import { login, updateUserTags } from './api/users'
import ArticleCard from './components/ArticleCard'
import FilterBar from './components/FilterBar'
import ProfileModal from './components/ProfileModal'
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
  const [sources, setSources] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedTags, setSelectedTags] = useState(() => readStoredProfile()?.tags || [])
  const [matchMode, setMatchMode] = useState(() => readStoredProfile()?.match_mode || 'or')
  const [source, setSource] = useState('all')
  const [profile, setProfile] = useState(() => readStoredProfile())
  const [isProfileOpen, setIsProfileOpen] = useState(() => !readStoredProfile())

  useEffect(() => {
    let isActive = true

    async function loadOptions() {
      const [nextTags, nextSources] = await Promise.all([getTags(), getArticleSources()])
      if (isActive) {
        setAllTags(nextTags.map((tag) => tag.id))
        setSources(nextSources)
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
      const nextArticles = await getArticles({
        tags: selectedTags,
        match: matchMode,
        source,
        q: searchTerm,
      })
      if (isActive) {
        setArticles(nextArticles)
        setIsLoading(false)
      }
    }

    loadArticles()

    return () => {
      isActive = false
    }
  }, [matchMode, searchTerm, selectedTags, source])

  async function handleProfileSave(nextProfile) {
    const savedProfile = await login(nextProfile)
    const profileWithTags = {
      ...savedProfile,
      tags: nextProfile.tags,
      match_mode: nextProfile.match_mode,
    }

    await updateUserTags(profileWithTags.user_id, nextProfile.tags, nextProfile.match_mode)

    window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profileWithTags))
    setProfile(profileWithTags)
    setSelectedTags(nextProfile.tags)
    setMatchMode(nextProfile.match_mode)
    setIsProfileOpen(false)
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Scicommons prototype</p>
          <h1>Daily academic articles</h1>
        </div>
        <button className="profile-button" type="button" onClick={() => setIsProfileOpen(true)}>
          {profile?.username || 'Set profile'}
        </button>
      </header>

      <section className="feed-layout" aria-label="Article feed">
        <FilterBar
          allTags={allTags}
          matchMode={matchMode}
          onMatchModeChange={setMatchMode}
          onSearchChange={setSearchTerm}
          onSelectedTagsChange={setSelectedTags}
          onSourceChange={setSource}
          searchTerm={searchTerm}
          selectedTags={selectedTags}
          source={source}
          sources={sources}
        />

        <div className="feed-summary">
          <span>{isLoading ? 'Loading articles' : `${articles.length} articles`}</span>
          <span>Mock data for frontend/backend integration</span>
        </div>

        <div className="article-list">
          {articles.map((article) => (
            <ArticleCard key={article.paper_key} article={article} />
          ))}

          {!isLoading && articles.length === 0 && (
            <div className="empty-state">
              <h2>No matching articles</h2>
              <p>Try removing a tag or switching the match mode.</p>
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
