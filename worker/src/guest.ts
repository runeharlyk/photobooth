import { CONTENT_TYPES, downloadName, isId, markerKey, mediaKey } from './capture'
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
  const [id, child, ...rest] = segments
  if (request.method !== 'GET' || id === undefined || !isId(id) || rest.length > 0) return null
  if (child === undefined) return page(bucket, id)
  if (child === 'status') return status(bucket, id)
  if (isPublicFile(child)) return file(bucket, id, child, new URL(request.url).searchParams.get('download') === '1')
  return null
}

function isPublicFile(value: string): value is PublicFile {
  return (PUBLIC_FILES as readonly string[]).includes(value)
}

async function readStatus(bucket: R2Bucket, id: string): Promise<Status> {
  const exists = async (key: string) => (await bucket.head(key)) !== null
  const [photo, boomerang, video] = await Promise.all([
    exists(mediaKey(id, 'photo.jpg')),
    exists(mediaKey(id, 'boomerang.mp4')),
    exists(mediaKey(id, 'video.mp4')),
  ])
  return { photo, boomerang, video, expired: !photo && (await exists(markerKey(id))) }
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
    headers.set('content-disposition', `attachment; filename="${downloadName(id, name)}"`)
  }
  return new Response(object.body, { headers })
}
