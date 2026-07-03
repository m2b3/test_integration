import { useState } from 'react'
import InterestInput from './InterestInput'

function ManageInterestsPage({ onBack, onSave, profile }) {
  const [tags, setTags] = useState(profile?.tags || [])
  const [authors, setAuthors] = useState(profile?.authors || '')

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
        <InterestInput interests={tags} onChange={setTags} />

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
