import { CONTENT_TYPES, isId, markerKey, mediaKey, mediaPrefix } from './capture'
import { renderPage } from './page'

export const PUBLIC_FILES = ['photo.jpg', 'boomerang.mp4', 'video.mp4'] as const

export type PublicFile = (typeof PUBLIC_FILES)[number]

export interface Status {
  photo: boolean
  boomerang: boolean
  video: boolean
  expired: boolean
}

export async function handleGuest(request: Request, bucket: R2Bucket, segments: string[]): Promise<Response | null> {
  const [id, child] = segments
  if (request.method !== 'GET' || !isId(id) || segments.length > 2) return null
  if (child === undefined) return page(bucket, id)
  if (child === 'status') return status(bucket, id)
  if (isPublicFile(child)) return file(bucket, id, child, new URL(request.url).searchParams.get('download') === '1')
  return null
}

function isPublicFile(value: string): value is PublicFile {
  return (PUBLIC_FILES as readonly string[]).includes(value)
}

async function readStatus(bucket: R2Bucket, id: string): Promise<Status> {
  const listing = await bucket.list({ prefix: mediaPrefix(id) })
  const stored = new Set(listing.objects.map((object) => object.key.slice(mediaPrefix(id).length)))
  const expired = stored.size === 0 && (await bucket.head(markerKey(id))) !== null
  return {
    photo: stored.has('photo.jpg'),
    boomerang: stored.has('boomerang.mp4'),
    video: stored.has('video.mp4'),
    expired,
  }
}

async function status(bucket: R2Bucket, id: string): Promise<Response> {
  return Response.json(await readStatus(bucket, id), { headers: { 'cache-control': 'no-store' } })
}

async function page(bucket: R2Bucket, id: string): Promise<Response> {
  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(18))))
  return new Response(renderPage(id, await readStatus(bucket, id), nonce), {
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'cache-control': 'no-store',
      'content-security-policy': [
        "default-src 'none'",
        `script-src 'nonce-${nonce}'`,
        `style-src 'nonce-${nonce}'`,
        "img-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
      ].join('; '),
      'referrer-policy': 'no-referrer',
      'x-content-type-options': 'nosniff',
      'x-robots-tag': 'noindex',
    },
  })
}

async function file(bucket: R2Bucket, id: string, name: PublicFile, download: boolean): Promise<Response | null> {
  const object = await bucket.get(mediaKey(id, name))
  if (object === null) return null
  const headers = new Headers({
    'content-type': CONTENT_TYPES[name],
    'content-length': String(object.size),
    'cache-control': 'private, max-age=3600',
    'x-content-type-options': 'nosniff',
  })
  if (download) {
    headers.set('content-disposition', `attachment; filename="photobooth-${id.slice(0, 8)}-${name}"`)
  }
  return new Response(object.body, { headers })
}
