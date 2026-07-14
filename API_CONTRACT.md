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
GET  /me
POST /logout
PUT  /users/{user_id}/tags
```

Article endpoints are implemented by proxying to the article/search API from
the `scicomm_embedding` repo. Configure the backend with:

```text
ARTICLE_SERVICE_BASE_URL=http://localhost:8100
```

`POST /login` creates a backend session in `user_sessions` and sets an HTTP-only
`scicommons_session` cookie. The frontend should send API requests with
credentials included, then use `GET /me` on refresh to verify the active user.
The frontend may cache the last profile and UI filters in local storage for a
faster remembered UI, but Postgres/session state remains the source of truth.

Login request:

```json
{
  "username": "u1",
  "email": "u1@example.com",
  "create_account": false
}
```

Rules:

```text
username and email are required
email must be valid
create_account=false logs in only an existing matching email/username pair
create_account=true creates only when the email does not already exist
existing email with create_account=true returns 409
existing email with wrong username returns 401
unknown email with create_account=false returns 404
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

User-specific endpoints require the active session cookie to match `{user_id}`.

All Feed does not require login:

```text
GET /articles
```

Shared query params:

```text
source=all|arxiv|pubmed|openreview|biorxiv|medrxiv|psyarxiv|socarxiv
tags=<comma-separated article category tags, e.g. math.NT,cs.LG>
match=or|and
semantic_query=<free text>
keyword_query=<free text>
search_mode=none|semantic|keyword|hybrid
date=YYYY-MM-DD
limit=<page size>
offset=<starting row>
```

Frontend search mode rule:

```text
semantic_query only -> semantic
keyword_query only  -> keyword
both filled         -> hybrid
neither filled      -> none
```

The backend forwards these params to the article/search service. All Feed
searches the whole article database and then applies source/tag filters.
Recommended uses the user's saved interests/authors as a semantic query when
the frontend has not provided an explicit search. When the frontend does
provide a search under Recommended, the backend sends the user interest query
as `scope_semantic_query` so article-service results are constrained to the
approximate recommendation scope until a persisted `user_daily_feed` exists.

Article-service-only params used behind the backend:

```text
tag_match=or|and
scope_semantic_query=<user interests/authors>
scope_limit=<maximum recommendation-scope candidate count>
```

## User Interests

Implemented:

```text
POST /login
GET /me
POST /logout
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
