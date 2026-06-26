export const users = [
  {
    id: 'user-1',
    email: 'u1@example.com',
  },
  {
    id: 'user-2',
    email: 'u2@example.com',
  },
  {
    id: 'user-3',
    email: 'u3@example.com',
  },
]

export const tags = [
  {
    id: 'biology',
    name: 'biology',
  },
  {
    id: 'machine-learning',
    name: 'machine learning',
  },
  {
    id: 'chemistry',
    name: 'chemistry',
  },
  {
    id: 'medicine',
    name: 'medicine',
  },
  {
    id: 'physics',
    name: 'physics',
  },
]

export const userTags = [
  {
    user_id: 'user-1',
    tag_id: 'biology',
  },
  {
    user_id: 'user-2',
    tag_id: 'machine-learning',
  },
  {
    user_id: 'user-3',
    tag_id: 'chemistry',
  },
  {
    user_id: 'user-3',
    tag_id: 'medicine',
  },
]

export const articles = [
  {
    id: 'article-1',
    title: 'bio paper',
    authors: 'bio author',
    source: 'biorxiv',
    url: '',
    published_date: '2026-06-26',
    abstract: 'asdf biology mock abstract text',
  },
  {
    id: 'article-2',
    title: 'machine learning paper',
    authors: 'machine learning author',
    source: 'arxiv',
    url: '',
    published_date: '2026-06-26',
    abstract: 'asdf machine learning mock abstract text',
  },
  {
    id: 'article-3',
    title: 'chemistry paper',
    authors: 'chem author',
    source: 'pubmed',
    url: '',
    published_date: '2026-06-26',
    abstract: 'asdf chemistry mock abstract text',
  },
  {
    id: 'article-4',
    title: 'medicine paper',
    authors: 'medicine author',
    source: 'medrxiv',
    url: '',
    published_date: '2026-06-26',
    abstract: 'asdf medicine mock abstract text',
  },
  {
    id: 'article-5',
    title: 'physics paper',
    authors: 'physics author',
    source: 'arxiv',
    url: '',
    published_date: '2026-06-26',
    abstract: 'asdf physics mock abstract text',
  },
  {
    id: 'article-6',
    title: 'chem and bio paper',
    authors: 'chem author, bio author',
    source: 'biorxiv',
    url: 'test text',
    published_date: '2026-06-26',
    abstract: 'asdf chemistry biology mock abstract text',
  },
  {
    id: 'article-7',
    title: 'bio and machine learning paper',
    authors: 'bio author, machine learning author',
    source: 'arxiv',
    url: 'test text',
    published_date: '2026-06-26',
    abstract: 'asdf biology machine learning mock abstract text',
  },
  {
    id: 'article-8',
    title: 'medicine and chemistry paper',
    authors: 'medicine author, chem author',
    source: 'pubmed',
    url: 'test text',
    published_date: '2026-06-26',
    abstract: 'asdf medicine chemistry mock abstract text',
  },
]

export const articleTags = [
  {
    article_id: 'article-1',
    tag_id: 'biology',
  },
  {
    article_id: 'article-2',
    tag_id: 'machine-learning',
  },
  {
    article_id: 'article-3',
    tag_id: 'chemistry',
  },
  {
    article_id: 'article-4',
    tag_id: 'medicine',
  },
  {
    article_id: 'article-5',
    tag_id: 'physics',
  },
  {
    article_id: 'article-6',
    tag_id: 'chemistry',
  },
  {
    article_id: 'article-6',
    tag_id: 'biology',
  },
  {
    article_id: 'article-7',
    tag_id: 'biology',
  },
  {
    article_id: 'article-7',
    tag_id: 'machine-learning',
  },
  {
    article_id: 'article-8',
    tag_id: 'medicine',
  },
  {
    article_id: 'article-8',
    tag_id: 'chemistry',
  },
]

export const userDailyFeed = [
  {
    user_id: 'user-1',
    article_id: 'article-1',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-1',
    article_id: 'article-6',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-1',
    article_id: 'article-7',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-2',
    article_id: 'article-2',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-2',
    article_id: 'article-7',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-3',
    article_id: 'article-3',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-3',
    article_id: 'article-6',
    feed_date: '2026-06-26',
  },
  {
    user_id: 'user-3',
    article_id: 'article-8',
    feed_date: '2026-06-26',
  },
]
