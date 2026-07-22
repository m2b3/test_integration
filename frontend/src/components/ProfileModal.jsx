import { useRef, useState } from 'react'
import AuthorInput, { normalizeAuthors } from './AuthorInput'
import InterestInput from './InterestInput'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function ProfileModal({ initialProfile, onClose, onLogin, onSaveInterests }) {
  const [step, setStep] = useState('account')
  const [username, setUsername] = useState(initialProfile?.username || '')
  const [email, setEmail] = useState(initialProfile?.email || '')
  const [tags, setTags] = useState(initialProfile?.tags || [])
  const [authors, setAuthors] = useState(() => normalizeAuthors(initialProfile?.authors))
  const [createdProfile, setCreatedProfile] = useState(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const interestInputRef = useRef(null)
  const authorInputRef = useRef(null)

  function validateAccountFields() {
    if (!username.trim()) {
      return 'Username is required.'
    }
    if (!EMAIL_RE.test(email.trim())) {
      return 'Enter a valid email address.'
    }
    return ''
  }

  async function submitAccount(createAccount = false) {
    const validationError = validateAccountFields()
    if (validationError) {
      setErrorMessage(validationError)
      return
    }

    setIsSubmitting(true)
    setErrorMessage('')

    try {
      const profile = await onLogin({
        username: username.trim(),
        email: email.trim(),
        createAccount,
      })

      if (createAccount) {
        setCreatedProfile(profile)
        setTags(profile.tags || [])
        setAuthors(normalizeAuthors(profile.authors))
        setStep('interests')
      }
    } catch (error) {
      setErrorMessage(error.message || 'Could not complete account request.')
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleAccountSubmit(event) {
    event.preventDefault()
    submitAccount(false)
  }

  function handleInterestSubmit(event) {
    event.preventDefault()
    const nextTags = interestInputRef.current?.commitPending() || tags
    const nextAuthors = authorInputRef.current?.commitPending() || authors
    onSaveInterests({
      ...createdProfile,
      tags: nextTags,
      authors: nextAuthors,
    })
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="profile-modal" role="dialog" aria-modal="true" aria-labelledby="profile-title">
        <div className="modal-header">
          <div>
            <p className="eyebrow">{step === 'account' ? 'Account' : 'Interests'}</p>
            <h2 id="profile-title">{step === 'account' ? 'Log in or create account' : 'Choose fields'}</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close profile" onClick={onClose}>
            x
          </button>
        </div>

        {step === 'account' ? (
          <form onSubmit={handleAccountSubmit}>
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
                  placeholder="u1@example.com"
                  type="email"
                  value={email}
                />
              </label>
            </div>

            {errorMessage && <p className="form-error">{errorMessage}</p>}

            <div className="modal-actions">
              <button className="secondary-button" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="secondary-button"
                disabled={isSubmitting}
                type="button"
                onClick={() => submitAccount(true)}
              >
                Create account
              </button>
              <button className="primary-button" disabled={isSubmitting} type="submit">
                Log in
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handleInterestSubmit}>
            <InterestInput ref={interestInputRef} interests={tags} onChange={setTags} />
            <AuthorInput ref={authorInputRef} authors={authors} onChange={setAuthors} />

            <div className="modal-actions">
              <button className="secondary-button" type="button" onClick={() => setStep('account')}>
                Back
              </button>
              <button className="primary-button" type="submit">
                Save interests
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  )
}

export default ProfileModal
