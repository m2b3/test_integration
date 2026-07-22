import { useRef, useState } from 'react'
import AuthorInput, { normalizeAuthors } from './AuthorInput'
import InterestInput from './InterestInput'

function ManageInterestsPage({ onBack, onSave, profile }) {
  const [tags, setTags] = useState(profile?.tags || [])
  const [authors, setAuthors] = useState(() => normalizeAuthors(profile?.authors))
  const [errorMessage, setErrorMessage] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const interestInputRef = useRef(null)
  const authorInputRef = useRef(null)

  async function handleSubmit(event) {
    event.preventDefault()
    const nextTags = interestInputRef.current?.commitPending() || tags
    const nextAuthors = authorInputRef.current?.commitPending() || authors
    setErrorMessage('')
    setIsSaving(true)
    try {
      await onSave({
        ...profile,
        tags: nextTags,
        authors: nextAuthors,
      })
    } catch (error) {
      setErrorMessage(error.message || 'Could not save interests.')
    } finally {
      setIsSaving(false)
    }
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
        <InterestInput ref={interestInputRef} interests={tags} onChange={setTags} />
        <AuthorInput ref={authorInputRef} authors={authors} onChange={setAuthors} />

        {errorMessage && <p className="form-error">{errorMessage}</p>}

        <div className="modal-actions">
          <button className="primary-button" disabled={isSaving} type="submit">
            {isSaving ? 'Saving interests' : 'Save interests'}
          </button>
        </div>
      </form>
    </section>
  )
}

export default ManageInterestsPage
