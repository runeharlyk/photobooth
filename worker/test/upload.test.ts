import { env } from 'cloudflare:workers'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CONTENT_TYPES, newId, upload, validMeta, type Name } from './helpers'

async function storedKeys(id: string): Promise<string[]> {
  const media = await env.SHARES.list({ prefix: `media/${id}/` })
  const marker = await env.SHARES.head(`markers/${id}`)
  return [...media.objects.map((object) => object.key), ...(marker ? [marker.key] : [])]
}

function meta(captured_at: unknown, event: unknown): string {
  return JSON.stringify({ captured_at, event })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('upload authentication', () => {
  it('rejects a request without a token and stores nothing', async () => {
    const id = newId()
    const response = await upload(id, 'photo.jpg', 'jpeg-bytes', { authorization: null })
    expect(response.status).toBe(401)
    expect(await storedKeys(id)).toEqual([])
  })

  it.each([
    ['a wrong token of the same length', 'Bearer test-upload-tokem'],
    ['a wrong token of another length', 'Bearer nope'],
    ['a prefix of the token', 'Bearer test-upload'],
    ['the token with a suffix', 'Bearer test-upload-token2'],
    ['the right token under another scheme', 'Basic test-upload-token'],
    ['the right token without a scheme', 'test-upload-token'],
    ['an empty bearer token', 'Bearer '],
  ])('rejects %s and stores nothing', async (_case, authorization) => {
    const id = newId()
    const response = await upload(id, 'photo.jpg', 'jpeg-bytes', { authorization })
    expect(response.status).toBe(401)
    expect(await storedKeys(id)).toEqual([])
  })

  it('accepts the right token and stores the file with its content type', async () => {
    const id = newId()
    const response = await upload(id, 'photo.jpg', 'jpeg-bytes')
    expect(response.status).toBe(204)
    const object = await env.SHARES.get(`media/${id}/photo.jpg`)
    expect(await object?.text()).toBe('jpeg-bytes')
    expect(object?.httpMetadata?.contentType).toBe('image/jpeg')
  })

  it('compares the token in constant time', async () => {
    const timingSafeEqual = vi.spyOn(crypto.subtle, 'timingSafeEqual')
    await upload(newId(), 'photo.jpg', 'jpeg-bytes', { authorization: 'Bearer wrong' })
    await upload(newId(), 'photo.jpg', 'jpeg-bytes')
    expect(timingSafeEqual).toHaveBeenCalledTimes(2)
  })

  it('checks the token before revealing whether the path is valid', async () => {
    const response = await upload('short', 'photo.jpg', 'jpeg-bytes', { authorization: null })
    expect(response.status).toBe(401)
  })
})

describe('upload validation', () => {
  it.each(Object.entries(CONTENT_TYPES))('accepts %s as %s', async (name, contentType) => {
    const id = newId()
    const body = name === 'meta.json' ? validMeta() : `${name}-bytes`
    const response = await upload(id, name, body, { 'content-type': contentType })
    expect(response.status).toBe(204)
    const object = await env.SHARES.get(`media/${id}/${name}`)
    expect(await object?.text()).toBe(body)
    expect(object?.httpMetadata?.contentType).toBe(contentType)
  })

  it('accepts a content type with parameters and stores the bare type', async () => {
    const id = newId()
    const response = await upload(id, 'meta.json', validMeta(), {
      'content-type': 'Application/JSON; charset=utf-8',
    })
    expect(response.status).toBe(204)
    const object = await env.SHARES.head(`media/${id}/meta.json`)
    expect(object?.httpMetadata?.contentType).toBe('application/json')
  })

  it.each([
    ['too short', 'AAAAAAAAAAAAAAAAAAAAA'],
    ['too long', 'AAAAAAAAAAAAAAAAAAAAAAA'],
    ['a dot', 'AAAAAAAAAAAAAAAAAAAAA.'],
    ['an encoded slash', 'AAAAAAAAAAAAAAAAAAA%2F'],
    ['a plus sign', 'AAAAAAAAAAAAAAAAAAAAA+'],
  ])('rejects an id that is %s', async (_case, id) => {
    const response = await upload(id, 'photo.jpg', 'jpeg-bytes')
    expect(response.status).toBe(400)
    expect((await env.SHARES.list()).objects.filter((object) => object.key.includes(id))).toEqual([])
  })

  it.each(['photo.png', 'clip.mp4', 'PHOTO.JPG', 'meta.json.bak', 'qr.svg'])(
    'rejects the name %s',
    async (name) => {
      const id = newId()
      const response = await upload(id, name, 'bytes', { 'content-type': 'image/jpeg' })
      expect(response.status).toBe(400)
      expect(await storedKeys(id)).toEqual([])
    },
  )

  it.each([
    ['photo.jpg', 'video/mp4'],
    ['photo.jpg', 'image/png'],
    ['photo.jpg', 'image/jpegx'],
    ['photo.jpg', ''],
    ['photo.jpg', null],
    ['boomerang.mp4', 'image/jpeg'],
    ['video.mp4', 'application/json'],
    ['meta.json', 'text/plain'],
  ])('rejects %s sent as %j', async (name, contentType) => {
    const id = newId()
    const body = new TextEncoder().encode(name === 'meta.json' ? validMeta() : 'bytes')
    const response = await upload(id, name, body, { 'content-type': contentType })
    expect(response.status).toBe(400)
    expect(await storedKeys(id)).toEqual([])
  })

  it('rejects an empty body', async () => {
    const id = newId()
    const response = await upload(id, 'photo.jpg', new Uint8Array())
    expect(response.status).toBe(400)
    expect(await storedKeys(id)).toEqual([])
  })
})

describe('upload idempotence', () => {
  it('overwrites a re-uploaded file', async () => {
    const id = newId()
    expect((await upload(id, 'video.mp4', 'first')).status).toBe(204)
    expect((await upload(id, 'video.mp4', 'second')).status).toBe(204)
    const object = await env.SHARES.get(`media/${id}/video.mp4`)
    expect(await object?.text()).toBe('second')
  })
})

describe('upload marker', () => {
  it('is written on the first successful upload with the upload time', async () => {
    const id = newId()
    const before = Date.now()
    await upload(id, 'photo.jpg', 'jpeg-bytes')
    const marker = await env.SHARES.get(`markers/${id}`)
    const body = (await marker?.json()) as { uploaded_at: string }
    expect(Object.keys(body)).toEqual(['uploaded_at'])
    expect(body.uploaded_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
    expect(Date.parse(body.uploaded_at)).toBeGreaterThanOrEqual(before - 1000)
    expect(Date.parse(body.uploaded_at)).toBeLessThanOrEqual(Date.now() + 1000)
  })

  it('is never overwritten by later uploads of the same id', async () => {
    const id = newId()
    await upload(id, 'photo.jpg', 'jpeg-bytes')
    const original = await (await env.SHARES.get(`markers/${id}`))?.text()
    for (const name of ['meta.json', 'boomerang.mp4', 'video.mp4', 'photo.jpg'] as Name[]) {
      await new Promise((resolve) => setTimeout(resolve, 5))
      const body = name === 'meta.json' ? validMeta() : `${name}-bytes`
      expect((await upload(id, name, body)).status).toBe(204)
    }
    expect(await (await env.SHARES.get(`markers/${id}`))?.text()).toBe(original)
  })

  it('keeps a marker that predates the upload', async () => {
    const id = newId()
    const original = JSON.stringify({ uploaded_at: '2026-01-01T00:00:00.000Z' })
    await env.SHARES.put(`markers/${id}`, original)
    expect((await upload(id, 'photo.jpg', 'jpeg-bytes')).status).toBe(204)
    expect(await (await env.SHARES.get(`markers/${id}`))?.text()).toBe(original)
  })

  it('is not written by a rejected upload', async () => {
    const id = newId()
    await upload(id, 'photo.jpg', 'jpeg-bytes', { authorization: 'Bearer wrong' })
    await upload(id, 'photo.jpg', 'jpeg-bytes', { 'content-type': 'image/png' })
    await upload(id, 'meta.json', '{}')
    expect(await env.SHARES.head(`markers/${id}`)).toBeNull()
  })
})

describe('meta.json validation', () => {
  it('stores a valid document unchanged', async () => {
    const id = newId()
    const body = meta('2026-10-18T19:30:00.250Z', 'kitchen')
    expect((await upload(id, 'meta.json', body)).status).toBe(204)
    expect(await (await env.SHARES.get(`media/${id}/meta.json`))?.text()).toBe(body)
  })

  it.each([
    ['the booth format', meta('2026-10-18T19:30:00+00:00', 'kitchen')],
    ['a hyphenated event', meta('2026-10-18T19:30:00+00:00', 'annas-30th')],
    ['a 40-character event', meta('2026-10-18T19:30:00Z', `a${'b'.repeat(39)}`)],
    ['a digit-first event', meta('2026-10-18T19:30:00Z', '2026-party')],
    ['microseconds', meta('2026-10-18T19:30:00.123456+00:00', 'kitchen')],
    ['a leap day', meta('2028-02-29T00:00:00Z', 'kitchen')],
  ])('accepts %s', async (_case, body) => {
    expect((await upload(newId(), 'meta.json', body)).status).toBe(204)
  })

  it.each([
    ['invalid JSON', '{"captured_at": '],
    ['a JSON array', '[]'],
    ['JSON null', 'null'],
    ['a missing event', JSON.stringify({ captured_at: '2026-10-18T19:30:00+00:00' })],
    ['a missing capture time', JSON.stringify({ event: 'kitchen' })],
    ['an extra field', JSON.stringify({ captured_at: '2026-10-18T19:30:00Z', event: 'kitchen', id: 'x' })],
    ['an uppercase event', meta('2026-10-18T19:30:00Z', 'Kitchen')],
    ['a hyphen-first event', meta('2026-10-18T19:30:00Z', '-party')],
    ['an empty event', meta('2026-10-18T19:30:00Z', '')],
    ['a 41-character event', meta('2026-10-18T19:30:00Z', `a${'b'.repeat(40)}`)],
    ['an event with a space', meta('2026-10-18T19:30:00Z', 'the party')],
    ['a numeric event', meta('2026-10-18T19:30:00Z', 7)],
    ['a free-text time', meta('yesterday', 'kitchen')],
    ['a date without time', meta('2026-10-18', 'kitchen')],
    ['an impossible date', meta('2026-02-30T19:30:00Z', 'kitchen')],
    ['a non-leap February 29', meta('2026-02-29T19:30:00Z', 'kitchen')],
    ['an impossible hour', meta('2026-10-18T24:30:00Z', 'kitchen')],
    ['a non-UTC offset', meta('2026-10-18T21:30:00+02:00', 'kitchen')],
    ['a time without zone', meta('2026-10-18T19:30:00', 'kitchen')],
    ['a numeric time', meta(1760815800, 'kitchen')],
  ])('rejects %s', async (_case, body) => {
    const id = newId()
    expect((await upload(id, 'meta.json', body)).status).toBe(400)
    expect(await storedKeys(id)).toEqual([])
  })
})
