# Scicommons API Contract Notes

This file tracks frontend/backend API needs while the prototype evolves.

## Current Implemented Endpoints

```text
GET  /health
GET  /tags
GET  /sources
GET  /articles
GET  /users/{user_id}/feed
GET  /users/{user_id}/profile
PUT  /users/{user_id}/profile
GET  /users/{user_id}/tags
GET  /users/{user_id}/recently-viewed?limit=20
POST /users/{user_id}/recently-viewed
POST /login
PUT  /users/{user_id}/tags
```

## Feed Endpoints

The frontend has two main feed modes:

```text
Recommended
All Feed
```

Recommended requires a logged-in user:

```text
GET /users/{user_id}/feed
```

All Feed does not require login:

```text
GET /articles
```

Shared query params:

```text
source=all|arxiv|pubmed
semantic_query=<free text>
keyword_query=<free text>
search_mode=none|semantic|keyword|hybrid
date=YYYY-MM-DD
```

Frontend search mode rule:

```text
semantic_query only -> semantic
keyword_query only  -> keyword
both filled         -> hybrid
neither filled      -> none
```

Current backend accepts the extra search params but still returns simple SQL-filtered feed data.
Later, these params should route into the embedding/search pipeline.

## User Interests

Implemented:

```text
GET /users/{user_id}/profile
PUT /users/{user_id}/profile
GET /users/{user_id}/tags
PUT /users/{user_id}/tags
```

Current required preference:

```text
fields of interest as free-form strings
```

Likely future preferences:

```text
favorite_articles
recently_viewed
```

Suggested future endpoints:
Implemented recently viewed:

```text
GET  /users/{user_id}/recently-viewed?limit=20
POST /users/{user_id}/recently-viewed
```

`POST /users/{user_id}/recently-viewed` stores a snapshot so recently viewed still works after old rows are recycled from the rolling article cache:

```json
{
  "article_key": "arxiv:2401.12345",
  "source": "arxiv",
  "external_id": "2401.12345",
  "title": "Example paper",
  "authors": "A. Smith, B. Lee",
  "url": "https://arxiv.org/abs/2401.12345",
  "published_date": "2026-07-03",
  "abstract": "...",
  "tags": ["biology", "machine learning"]
}
```

Likely future endpoints:

```text
GET  /users/{user_id}/saved-articles
POST /users/{user_id}/saved-articles
DELETE /users/{user_id}/saved-articles/{article_id}
```
