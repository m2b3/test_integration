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

  if (filters.semantic_query) {
    params.set('semantic_query', filters.semantic_query)
  }

  if (filters.keyword_query) {
    params.set('keyword_query', filters.keyword_query)
  }

  if (filters.search_mode) {
    params.set('search_mode', filters.search_mode)
  }

  if (filters.date) {
    params.set('date', filters.date)
  }

  if (Number.isInteger(filters.limit)) {
    params.set('limit', String(filters.limit))
  }

  if (Number.isInteger(filters.offset)) {
    params.set('offset', String(filters.offset))
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
