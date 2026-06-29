import { requestJson } from './client'

function buildArticleQuery(filters = {}) {
  const params = new URLSearchParams()

  if (filters.tags?.length) {
    params.set('tags', filters.tags.join(','))
  }

  if (filters.match) {
    params.set('match', filters.match)
  }

  if (filters.source) {
    params.set('source', filters.source)
  }

  if (filters.q) {
    params.set('q', filters.q)
  }

  if (filters.date) {
    params.set('date', filters.date)
  }

  const query = params.toString()
  return query ? `?${query}` : ''
}

export async function getArticles(filters = {}) {
  return requestJson(`/articles${buildArticleQuery(filters)}`)
}

export async function getUserFeed(userId, filters = {}) {
  return requestJson(`/users/${userId}/feed${buildArticleQuery(filters)}`)
}

export async function getTags() {
  return requestJson('/tags')
}

export async function getArticleSources() {
  return requestJson('/sources')
}
