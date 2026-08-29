import { ref } from 'vue'

export type User = { id: number; username: string; role: 'admin' | 'user' }
export type ManagedUser = User & { is_active: boolean; created_at: string; last_login_at: string | null }
export type VideoQuality = 'best' | '2160' | '1440' | '1080' | '720' | '480' | '360'
export type UploadConfig = { maxBytes: number; extensions: string[]; accept: string }
export type UploadedFile = { id: string; filename: string; size: number; contentType: string }
export type MediaFile = {
  id: string
  filename: string
  kind: 'upload' | 'download'
  size: number
  contentType?: string
  createdAt?: string
  thumbnail?: string | null
  sourceUrl?: string | null
}

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
  if (!res.ok) throw new Error(await responseError(res))
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

export function getUploadConfig() {
  return request<UploadConfig>('/api/video/upload-config')
}

export function uploadVideo(file: File, onProgress: (value: number) => void) {
  let xhr: XMLHttpRequest | undefined
  const promise = new Promise<UploadedFile>((resolve, reject) => {
    xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/video/upload?filename=' + encodeURIComponent(file.name))
    xhr.withCredentials = true
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
    xhr.setRequestHeader('X-CSRF-Token', csrfToken())
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      if (xhr!.status === 401) user.value = null
      if (xhr!.status < 200 || xhr!.status >= 300) {
        try {
          reject(new Error((JSON.parse(xhr!.responseText) as { detail?: string }).detail || xhr!.statusText))
        } catch {
          reject(new Error(xhr!.statusText || 'Upload failed'))
        }
        return
      }
      resolve(JSON.parse(xhr!.responseText) as UploadedFile)
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
    xhr.onabort = () => reject(new Error('Upload cancelled'))
    xhr.send(file)
  })
  return { promise, abort: () => xhr?.abort() }
}

export function deleteUploadedFile(id: string) {
  return request<{ ok: boolean }>('/api/video/upload/' + id, { method: 'DELETE' })
}

export function listMediaFiles() {
  return request<MediaFile[]>('/api/video/files')
}

export function mediaFileUrl(id: string) {
  return '/api/video/file/' + id
}

export function errorMessage(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

export function downloadVideo(url: string, quality: VideoQuality) {
  return request<MediaFile>('/api/video/download', {
    method: 'POST',
    body: JSON.stringify({ url, quality }),
  })
}

async function responseError(response: Response) {
  const text = await response.text()
  try {
    const parsed = JSON.parse(text) as { detail?: string }
    return parsed.detail || text || response.statusText
  } catch {
    return text || response.statusText
  }
}
