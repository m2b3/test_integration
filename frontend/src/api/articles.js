import { articleTags, articles, tags, userDailyFeed } from '../data/mockDatabase'

function tagsForArticle(articleId) {
  return articleTags
    .filter((articleTag) => articleTag.article_id === articleId)
    .map((articleTag) => articleTag.tag_id)
}

function normalizeArticle(article) {
  return {
    ...article,
    paper_key: article.id,
    tags: tagsForArticle(article.id),
  }
}

function filterArticles(articleList, filters = {}) {
  const { tags: selectedTags = [], match = 'or', source = 'all', q = '' } = filters
  const normalizedSearch = q.trim().toLowerCase()

  return articleList.filter((article) => {
    const articleWithTags = normalizeArticle(article)
    const matchesSource = source === 'all' || article.source === source
    const matchesSearch =
      !normalizedSearch ||
      article.title.toLowerCase().includes(normalizedSearch) ||
      article.authors.toLowerCase().includes(normalizedSearch)
    const matchesTags =
      selectedTags.length === 0 ||
      (match === 'and'
        ? selectedTags.every((tag) => articleWithTags.tags.includes(tag))
        : selectedTags.some((tag) => articleWithTags.tags.includes(tag)))

    return matchesSource && matchesSearch && matchesTags
  })
}

export async function getArticles(filters = {}) {
  return Promise.resolve(filterArticles(articles, filters).map(normalizeArticle))
}

export async function getUserFeed(userId, filters = {}) {
  const feedArticleIds = new Set(
    userDailyFeed
      .filter((feedItem) => feedItem.user_id === userId)
      .map((feedItem) => feedItem.article_id),
  )

  const feedArticles = articles.filter((article) => feedArticleIds.has(article.id))
  return Promise.resolve(filterArticles(feedArticles, filters).map(normalizeArticle))
}

export async function getTags() {
  return Promise.resolve(
    tags.map((tag) => ({
      ...tag,
      count: articleTags.filter((articleTag) => articleTag.tag_id === tag.id).length,
    })),
  )
}

export async function getArticleSources() {
  return Promise.resolve(Array.from(new Set(articles.map((article) => article.source))).sort())
}
