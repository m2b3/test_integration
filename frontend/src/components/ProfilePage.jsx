function ProfilePage({ onBack, onLogout, onManageInterests, onRecentlyViewed, profile }) {
  const displayName = profile?.username || profile?.email || 'Profile'

  return (
    <section className="profile-page" aria-label="User profile">
      <button className="text-button" type="button" onClick={onBack}>
        Back to feed
      </button>

      <div className="profile-header">
        <div className="avatar large">{displayName.slice(0, 1).toUpperCase()}</div>
        <div>
          <h2>{displayName}</h2>
          <p>{profile?.email}</p>
        </div>
      </div>

      <div className="profile-actions">
        <button type="button" onClick={onRecentlyViewed}>
          Recently viewed
        </button>
        <button type="button" onClick={onManageInterests}>
          Manage interests
        </button>
      </div>

      <div className="profile-footer">
        <button className="logout-button" type="button" onClick={onLogout}>
          Log out
        </button>
      </div>
    </section>
  )
}

export default ProfilePage
