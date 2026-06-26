import { userTags, users } from '../data/mockDatabase'

const demoUsers = new Map(users.map((user) => [user.id, { ...user }]))
let demoUserTags = [...userTags]

function userIdFromProfile(profile) {
  const base = profile.email || profile.username || 'demo'
  return `demo-${base.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`
}

export async function login(profile) {
  const existingUser = users.find((user) => user.email === profile.email)
  const user = existingUser || {
    id: userIdFromProfile(profile),
    email: profile.email,
  }

  demoUsers.set(user.id, user)

  return Promise.resolve({
    user_id: user.id,
    username: profile.username,
    email: user.email,
    tags: await getUserTags(user.id),
  })
}

export async function getUserTags(userId) {
  return Promise.resolve(
    demoUserTags
      .filter((userTag) => userTag.user_id === userId)
      .map((userTag) => userTag.tag_id),
  )
}

export async function updateUserTags(userId, payload) {
  demoUserTags = demoUserTags.filter((userTag) => userTag.user_id !== userId)
  demoUserTags.push(
    ...payload.tags.map((tagId) => ({
      user_id: userId,
      tag_id: tagId,
    })),
  )

  return Promise.resolve({
    user_id: userId,
    tags: payload.tags,
    match_mode: payload.match_mode,
  })
}
