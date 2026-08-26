import { ref } from 'vue'

export type User = { id: number; username: string; role: 'admin' | 'user' }
export type ManagedUser = User & { is_active: boolean; created_at: string; last_login_at: string | null }

export const user = ref<User | null>(null)

function csrfToken() {
  const match = document.cookie.match(/(?:^|; )ducklingo_csrf=([^;]*)/)
  return match ? decodeURIComponent(match[1]) : ''
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const method = (init.method || 'GET').toUpperCase()
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (method !== 'GET' && path !== '/api/auth/login') headers.set('X-CSRF-Token', csrfToken())
  const res = await fetch(path, { credentials: 'include', ...init, headers })
  if (res.status === 401) user.value = null
  if (!res.ok) throw new Error((await res.text()) || res.statusText)
  return res.json() as Promise<T>
}

export function login(username: string, password: string) {
  return request<User>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }).then((next) => {
    user.value = next
    return next
  })
}

export function me() {
  return request<User>('/api/auth/me').then((next) => {
    user.value = next
    return next
  })
}

export function logout() {
  return request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }).then(() => {
    user.value = null
  })
}

export function changePassword(current_password: string, new_password: string) {
  return request<{ ok: boolean }>('/api/auth/password', { method: 'POST', body: JSON.stringify({ current_password, new_password }) })
}

export function listUsers() {
  return request<ManagedUser[]>('/api/auth/users')
}

export function createUser(username: string, password: string, role: 'user' | 'admin') {
  return request<ManagedUser>('/api/auth/users', { method: 'POST', body: JSON.stringify({ username, password, role }) })
}

export function patchUser(id: number, data: { is_active?: boolean; password?: string }) {
  return request<ManagedUser>(`/api/auth/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
}
