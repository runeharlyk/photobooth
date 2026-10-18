import { CONTENT_TYPES, isId, isName, markerKey, mediaKey, type Name } from './capture'

const EVENT = /^[a-z0-9][a-z0-9-]{0,39}$/
const UTC_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|\+00:00)$/

export async function handleUpload(request: Request, env: Env, id: string, name: string): Promise<Response> {
  if (!(await isAuthorized(request.headers.get('authorization'), env.UPLOAD_TOKEN))) {
    return new Response('Unauthorized', { status: 401, headers: { 'www-authenticate': 'Bearer' } })
  }
  if (!isId(id) || !isName(name)) return badRequest('Unknown capture id or file name')
  if (mediaType(request.headers.get('content-type')) !== CONTENT_TYPES[name]) {
    return badRequest(`${name} must be sent as ${CONTENT_TYPES[name]}`)
  }
  const body = await request.arrayBuffer()
  if (body.byteLength === 0) return badRequest('Empty body')
  if (name === 'meta.json' && !isValidMeta(body)) return badRequest('Invalid meta.json')

  await store(env.SHARES, id, name, body)
  return new Response(null, { status: 204 })
}

async function isAuthorized(header: string | null, secret: string | undefined): Promise<boolean> {
  if (!secret || !header?.startsWith('Bearer ')) return false
  const [given, expected] = await Promise.all([digest(header.slice('Bearer '.length)), digest(secret)])
  return crypto.subtle.timingSafeEqual(given, expected)
}

async function digest(value: string): Promise<ArrayBuffer> {
  return crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
}

function mediaType(header: string | null): string {
  return (header ?? '').split(';')[0].trim().toLowerCase()
}

function badRequest(message: string): Response {
  return new Response(message, { status: 400 })
}

function isValidMeta(body: ArrayBuffer): boolean {
  let meta: unknown
  try {
    meta = JSON.parse(new TextDecoder().decode(body))
  } catch {
    return false
  }
  if (typeof meta !== 'object' || meta === null || Array.isArray(meta)) return false
  const fields = meta as Record<string, unknown>
  const keys = Object.keys(fields).sort()
  return (
    keys.length === 2 &&
    keys[0] === 'captured_at' &&
    keys[1] === 'event' &&
    typeof fields.event === 'string' &&
    EVENT.test(fields.event) &&
    typeof fields.captured_at === 'string' &&
    isUtcTimestamp(fields.captured_at)
  )
}

function isUtcTimestamp(value: string): boolean {
  const match = UTC_TIMESTAMP.exec(value)
  if (!match) return false
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number)
  const date = new Date(Date.UTC(year, month - 1, day, hour, minute, second))
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day &&
    date.getUTCHours() === hour &&
    date.getUTCMinutes() === minute &&
    date.getUTCSeconds() === second
  )
}

async function store(bucket: R2Bucket, id: string, name: Name, body: ArrayBuffer): Promise<void> {
  await bucket.put(mediaKey(id, name), body, { httpMetadata: { contentType: CONTENT_TYPES[name] } })
  await bucket.put(markerKey(id), JSON.stringify({ uploaded_at: new Date().toISOString() }), {
    httpMetadata: { contentType: 'application/json' },
    onlyIf: new Headers({ 'if-none-match': '*' }),
  })
}
