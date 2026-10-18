export const CONTENT_TYPES = {
  'photo.jpg': 'image/jpeg',
  'boomerang.mp4': 'video/mp4',
  'video.mp4': 'video/mp4',
  'meta.json': 'application/json',
} as const

export type Name = keyof typeof CONTENT_TYPES

const ID = /^[A-Za-z0-9_-]{22}$/

export function isId(value: string): boolean {
  return ID.test(value)
}

export function isName(value: string): value is Name {
  return Object.hasOwn(CONTENT_TYPES, value)
}

export function mediaKey(id: string, name: Name): string {
  return `media/${id}/${name}`
}

export function downloadName(id: string, name: Name): string {
  return `photobooth-${id.slice(0, 8)}-${name}`
}

export function markerKey(id: string): string {
  return `markers/${id}`
}
