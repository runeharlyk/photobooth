import { exports } from 'cloudflare:workers'

export const TOKEN = 'test-upload-token'
export const ORIGIN = 'https://photo.runeharlyk.dk'

const ID_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'

export function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(22))
  return Array.from(bytes, (byte) => ID_ALPHABET[byte % 64]).join('')
}

export const CONTENT_TYPES = {
  'photo.jpg': 'image/jpeg',
  'boomerang.mp4': 'video/mp4',
  'video.mp4': 'video/mp4',
  'meta.json': 'application/json',
} as const

export type Name = keyof typeof CONTENT_TYPES

export function upload(
  id: string,
  name: string,
  body: BodyInit,
  overrides: Record<string, string | null> = {},
): Promise<Response> {
  const headers = new Headers({
    authorization: `Bearer ${TOKEN}`,
    'content-type': CONTENT_TYPES[name as Name] ?? 'application/octet-stream',
  })
  for (const [header, value] of Object.entries(overrides)) {
    if (value === null) headers.delete(header)
    else headers.set(header, value)
  }
  return exports.default.fetch(`${ORIGIN}/api/captures/${id}/${name}`, { method: 'PUT', headers, body })
}

export function get(path: string, init?: RequestInit): Promise<Response> {
  return exports.default.fetch(`${ORIGIN}${path}`, init)
}

export function validMeta(): string {
  return JSON.stringify({ captured_at: '2026-10-18T19:30:00+00:00', event: 'kitchen' })
}
