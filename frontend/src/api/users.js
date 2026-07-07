import { requestJson } from './client'

export async function login({ username, email, createAccount = false }) {
  return requestJson('/login', {
    method: 'POST',
    body: JSON.stringify({
      username,
      email,
      create_account: createAccount,
    }),
  })
}

export async function getCurrentUser() {
  return requestJson('/me')
}

export async function logout() {
  return requestJson('/logout', {
    method: 'POST',
  })
}

export async function getUserTags(userId) {
  return requestJson(`/users/${userId}/tags`)
}

export async function getUserProfile(userId) {
  return requestJson(`/users/${userId}/profile`)
}

export async function updateUserProfile(userId, profile) {
  const authors = Array.isArray(profile.authors)
    ? profile.authors
    : String(profile.authors || '')
        .split(',')
        .map((author) => author.trim())
        .filter(Boolean)

  return requestJson(`/users/${userId}/profile`, {
    method: 'PUT',
    body: JSON.stringify({
      username: profile.username,
      email: profile.email,
      tags: profile.tags || [],
      authors,
    }),
  })
}

export async function updateUserTags(userId, tags, matchMode = 'or') {
  return requestJson(`/users/${userId}/tags`, {
    method: 'PUT',
    body: JSON.stringify({
      tags,
      match_mode: matchMode,
    }),
  })
}

export async function getRecentlyViewed(userId, limit = 20) {
  return requestJson(`/users/${userId}/recently-viewed?limit=${limit}`)
}

export async function addRecentlyViewed(userId, article) {
  return requestJson(`/users/${userId}/recently-viewed`, {
    method: 'POST',
    body: JSON.stringify({
      article_key: article.paper_key || article.id,
      id: article.id,
      source: article.source,
      external_id: article.external_id || article.id,
      title: article.title,
      authors: Array.isArray(article.authors) ? article.authors.join(', ') : article.authors || '',
      url: article.url || '',
      published_date: article.published_date || '',
      abstract: article.abstract || '',
      tags: article.tags || [],
    }),
  })
}
