import { useState } from 'react'

function ProfileModal({ allTags, initialProfile, onClose, onSave }) {
  const [username, setUsername] = useState(initialProfile?.username || '')
  const [email, setEmail] = useState(initialProfile?.email || '')
  const [tags, setTags] = useState(initialProfile?.tags || [])
  const [matchMode, setMatchMode] = useState(initialProfile?.match_mode || 'or')

  function toggleTag(tag) {
    setTags((currentTags) =>
      currentTags.includes(tag)
        ? currentTags.filter((currentTag) => currentTag !== tag)
        : [...currentTags, tag],
    )
  }

  function handleSubmit(event) {
    event.preventDefault()
    onSave({
      username: username.trim() || 'Demo User',
      email: email.trim() || 'demo@example.com',
      tags,
      match_mode: matchMode,
    })
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="profile-modal" role="dialog" aria-modal="true" aria-labelledby="profile-title">
        <div className="modal-header">
          <div>
            <p className="eyebrow">Demo profile</p>
            <h2 id="profile-title">Set article interests</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close profile" onClick={onClose}>
            x
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="profile-fields">
            <label className="field">
              <span>Username</span>
              <input
                onChange={(event) => setUsername(event.target.value)}
                placeholder="Jessica"
                type="text"
                value={username}
              />
            </label>
            <label className="field">
              <span>Email</span>
              <input
                onChange={(event) => setEmail(event.target.value)}
                placeholder="jessica@example.com"
                type="email"
                value={email}
              />
            </label>
          </div>

          <div className="profile-match">
            <span>Interest match</span>
            <div className="match-control">
              <button
                className={matchMode === 'or' ? 'is-active' : ''}
                type="button"
                onClick={() => setMatchMode('or')}
              >
                OR
              </button>
              <button
                className={matchMode === 'and' ? 'is-active' : ''}
                type="button"
                onClick={() => setMatchMode('and')}
              >
                AND
              </button>
            </div>
          </div>

          <div className="profile-tags" aria-label="Interest tags">
            {allTags.map((tag) => (
              <button
                className={tags.includes(tag) ? 'is-selected' : ''}
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
              >
                {tag}
              </button>
            ))}
          </div>

          <div className="modal-actions">
            <button className="secondary-button" type="button" onClick={onClose}>
              Cancel
            </button>
            <button className="primary-button" type="submit">
              Save profile
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

export default ProfileModal
