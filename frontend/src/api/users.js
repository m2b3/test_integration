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