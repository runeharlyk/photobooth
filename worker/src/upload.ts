import { CONTENT_TYPES, isId, isName, markerKey, mediaKey, type Name } from './capture'

const BEARER = /^Bearer (.+)$/i
const EVENT =/^[a-z0-9][a-z0-9-]{0,39}$/
const UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$/

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
  const token = BEARER.exec(header ?? '')?.[1]
  if (!secret || token === undefined) return false
  const [given, expected] = await Promise.all([digest(token), digest(secret)])
  return crypto.subtle.timingSafeEqual(given, expected)
}

async function digest(value: string): Promise<ArrayBuffer> {
  return crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
}

function mediaType(header: string | null): string {
  const [type = ''] = (header ?? '').split(';')
  return type.trim().toLowerCase()
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
  return (
    typeof fields.event === 'string' &&
    EVENT.test(fields.event) &&
    typeof fields.captured_at === 'string' &&
    isUtcTimestamp(fields.captured_at)
  )
}

function isUtcTimestamp(value: string): boolean {
  if (!UTC_TIMESTAMP.test(value)) return false
  const wholeSeconds = value.slice(0, 19)
  const parsed = Date.parse(`${wholeSeconds}Z`)
  return !Number.isNaN(parsed) && new Date(parsed).toISOString().startsWith(wholeSeconds)
}

async function store(bucket: R2Bucket, id: string, name: Name, body: ArrayBuffer): Promise<void> {
  await bucket.put(mediaKey(id, name), body, { httpMetadata: { contentType: CONTENT_TYPES[name] } })
  await bucket.put(markerKey(id), JSON.stringify({ uploaded_at: new Date().toISOString() }), {
    httpMetadata: { contentType: 'application/json' },
    onlyIf: new Headers({ 'if-none-match': '*' }),
  })
}
