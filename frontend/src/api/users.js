import { requestJson } from './client'

export async function login({ username = 'Demo User', email }) {
  return requestJson('/login', {
    method: 'POST',
    body: JSON.stringify({ username, email }),
  })
}

export async function getUserTags(userId) {
  return requestJson(`/users/${userId}/tags`)
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
