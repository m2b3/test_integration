import { useState } from 'react'
import InterestInput from './InterestInput'

function ProfileModal({ initialProfile, onClose, onSave }) {
  const [step, setStep] = useState(initialProfile ? 'interests' : 'account')
  const [username, setUsername] = useState(initialProfile?.username || '')
  const [email, setEmail] = useState(initialProfile?.email || '')
  const [tags, setTags] = useState(initialProfile?.tags || [])
  const [authors, setAuthors] = useState(initialProfile?.authors || '')

  function handleAccountSubmit(event) {
    event.preventDefault()
    setStep('interests')
  }

  function handleInterestSubmit(event) {
    event.preventDefault()
    onSave({
      username: username.trim() || 'Demo User',
      email: email.trim() || 'demo@example.com',
      tags,
      authors: authors.trim(),
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

            <div className="modal-actions">
              <button className="secondary-button" type="button" onClick={onClose}>
                Cancel
              </button>
              <button className="primary-button" type="submit">
                Continue
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handleInterestSubmit}>
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
              {!initialProfile && (
                <button className="secondary-button" type="button" onClick={() => setStep('account')}>
                  Back
                </button>
              )}
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
