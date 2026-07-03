import { useState } from 'react'

function ManageInterestsPage({ allTags, onBack, onSave, profile }) {
  const [tags, setTags] = useState(profile?.tags || [])
  const [authors, setAuthors] = useState(profile?.authors || '')

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
      ...profile,
      tags,
      authors: authors.trim(),
    })
  }

  return (
    <section className="profile-page" aria-label="Manage interests">
      <button className="text-button" type="button" onClick={onBack}>
        Back to profile
      </button>

      <div className="page-heading">
        <h2>Manage interests</h2>
      </div>

      <form className="interests-form" onSubmit={handleSubmit}>
        <div className="interest-section">
          <span>Fields of interest</span>
          <div className="profile-tags" aria-label="Interest fields">
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
        </div>

        <label className="field optional-field">
          <span>Authors to follow optional</span>
          <input
            onChange={(event) => setAuthors(event.target.value)}
            placeholder="Author names separated by commas"
            type="text"
            value={authors}
          />
        </label>

        <div className="modal-actions">
          <button className="primary-button" type="submit">
            Save interests
          </button>
        </div>
      </form>
    </section>
  )
}

export default ManageInterestsPage
