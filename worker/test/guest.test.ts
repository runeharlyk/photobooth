import { env } from 'cloudflare:workers'
import { describe, expect, it } from 'vitest'
import { get, newId, upload, validMeta } from './helpers'

async function seed(id: string, names: string[]): Promise<void> {
  for (const name of names) {
    const body = name === 'meta.json' ? validMeta() : `${name}-bytes`
    expect((await upload(id, name, body)).status).toBe(204)
  }
}

async function expire(id: string): Promise<void> {
  const media = await env.SHARES.list({ prefix: `media/${id}/` })
  await env.SHARES.delete(media.objects.map((object) => object.key))
}

async function status(id: string): Promise<unknown> {
  const response = await get(`/p/${id}/status`)
  expect(response.status).toBe(200)
  expect(response.headers.get('content-type')).toMatch(/^application\/json/)
  expect(response.headers.get('cache-control')).toBe('no-store')
  return response.json()
}

function section(html: string, name: string): { visible: boolean; content: string } {
  const match = new RegExp(`<section id="${name}"([^>]*)>([\\s\\S]*?)</section>`).exec(html)
  expect(match, `section ${name}`).not.toBeNull()
  return { visible: !/\bhidden\b/.test(match![1]), content: match![2] }
}

function visibleSections(html: string): string[] {
  return ['waiting', 'ready', 'expired'].filter((name) => section(html, name).visible)
}

function downloadLink(html: string, name: string): string {
  const match = new RegExp(`<a [^>]*data-name="${name.replace('.', '\\.')}"[^>]*>`).exec(html)
  expect(match, `download link for ${name}`).not.toBeNull()
  return match![0]
}

async function page(id: string): Promise<{ response: Response; html: string }> {
  const response = await get(`/p/${id}`)
  return { response, html: await response.text() }
}

describe('status', () => {
  it('reports nothing for an id that has not arrived', async () => {
    expect(await status(newId())).toEqual({ photo: false, boomerang: false, video: false, expired: false })
  })

  it('reports the photo once only the photo has arrived', async () => {
    const id = newId()
    await seed(id, ['photo.jpg'])
    expect(await status(id)).toEqual({ photo: true, boomerang: false, video: false, expired: false })
  })

  it('reports all three files once they have arrived', async () => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json', 'boomerang.mp4', 'video.mp4'])
    expect(await status(id)).toEqual({ photo: true, boomerang: true, video: true, expired: false })
  })

  it('reports expired when the marker remains but the media are gone', async () => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json', 'boomerang.mp4', 'video.mp4'])
    await expire(id)
    expect(await status(id)).toEqual({ photo: false, boomerang: false, video: false, expired: true })
  })

  it('does not report expired while meta.json is still stored', async () => {
    const id = newId()
    await seed(id, ['meta.json'])
    expect(await status(id)).toEqual({ photo: false, boomerang: false, video: false, expired: false })
  })
})

describe('file download', () => {
  it.each([
    ['photo.jpg', 'image/jpeg'],
    ['boomerang.mp4', 'video/mp4'],
    ['video.mp4', 'video/mp4'],
  ])('serves %s inline with its content type', async (name, contentType) => {
    const id = newId()
    await seed(id, [name])
    const response = await get(`/p/${id}/${name}`)
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe(contentType)
    expect(response.headers.get('content-disposition')).toBeNull()
    expect(new TextDecoder().decode(await response.arrayBuffer())).toBe(`${name}-bytes`)
  })

  it.each([
    ['photo.jpg', 'photo.jpg'],
    ['boomerang.mp4', 'boomerang.mp4'],
    ['video.mp4', 'video.mp4'],
  ])('serves %s as an attachment named by kind with ?download=1', async (name, suffix) => {
    const id = newId()
    await seed(id, [name])
    const response = await get(`/p/${id}/${name}?download=1`)
    expect(response.status).toBe(200)
    expect(response.headers.get('content-disposition')).toBe(
      `attachment; filename="photobooth-${id.slice(0, 8)}-${suffix}"`,
    )
    expect(new TextDecoder().decode(await response.arrayBuffer())).toBe(`${name}-bytes`)
  })

  it('returns 404 for a file that has not arrived', async () => {
    const id = newId()
    await seed(id, ['photo.jpg'])
    expect((await get(`/p/${id}/video.mp4`)).status).toBe(404)
    expect((await get(`/p/${id}/boomerang.mp4?download=1`)).status).toBe(404)
  })

  it('never serves meta.json', async () => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json'])
    for (const path of [`/p/${id}/meta.json`, `/p/${id}/meta.json?download=1`]) {
      const response = await get(path)
      expect(response.status).toBe(404)
      expect(await response.text()).not.toContain('kitchen')
    }
  })
})

describe('guest page', () => {
  it('renders the waiting state and polls the status every 3 s', async () => {
    const id = newId()
    const { response, html } = await page(id)
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('text/html; charset=utf-8')
    expect(response.headers.get('cache-control')).toBe('no-store')
    expect(visibleSections(html)).toEqual(['waiting'])
    expect(section(html, 'waiting').content).toContain('Getting your photos ready...')
    expect(html).toContain(`/p/${id}/status`)
    expect(html).toContain('3000')
    expect(html).not.toContain(`src="/p/${id}/photo.jpg"`)
  })

  it('renders the ready state with only the photo download enabled', async () => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json'])
    const { html } = await page(id)
    expect(visibleSections(html)).toEqual(['ready'])
    expect(section(html, 'ready').content).toContain(`src="/p/${id}/photo.jpg"`)
    expect(downloadLink(html, 'photo.jpg')).toContain(`href="/p/${id}/photo.jpg?download=1"`)
    for (const name of ['boomerang.mp4', 'video.mp4']) {
      expect(downloadLink(html, name)).toContain('aria-disabled="true"')
      expect(downloadLink(html, name)).not.toContain('href=')
    }
    expect(html).toContain(`/p/${id}/status`)
  })

  it('renders the ready state with every download enabled once all files exist', async () => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json', 'boomerang.mp4', 'video.mp4'])
    const { html } = await page(id)
    expect(visibleSections(html)).toEqual(['ready'])
    for (const name of ['photo.jpg', 'boomerang.mp4', 'video.mp4']) {
      expect(downloadLink(html, name)).toContain(`href="/p/${id}/${name}?download=1"`)
      expect(downloadLink(html, name)).not.toContain('aria-disabled')
    }
  })

  it('renders the expired state', async () => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json'])
    await expire(id)
    const { response, html } = await page(id)
    expect(response.status).toBe(200)
    expect(visibleSections(html)).toEqual(['expired'])
    expect(section(html, 'expired').content).toContain('These photos have expired.')
    expect(html).not.toContain(`src="/p/${id}/photo.jpg"`)
  })

  it('makes no third-party requests and leaks no referrer', async () => {
    const { response, html } = await page(newId())
    expect(html).not.toMatch(/(src|href|action)="(https?:)?\/\//)
    expect(html).not.toMatch(/url\(\s*['"]?(https?:)?\/\//)
    expect(html).not.toMatch(/@import/)
    const policy = response.headers.get('content-security-policy') ?? ''
    expect(policy).toContain("default-src 'none'")
    expect(policy).toContain("connect-src 'self'")
    expect(policy).toContain("img-src 'self'")
    expect(response.headers.get('referrer-policy')).toBe('no-referrer')
  })

  it('runs only the scripts and styles it was rendered with', async () => {
    const { response, html } = await page(newId())
    const nonce = /script-src 'nonce-([^']+)'/.exec(response.headers.get('content-security-policy') ?? '')?.[1]
    expect(nonce).toMatch(/^[A-Za-z0-9+/=_-]{16,}$/)
    expect(html).toContain(`<script nonce="${nonce}">`)
    expect(html).toContain(`<style nonce="${nonce}">`)
    expect(html.match(/<(script|style)\b/g)).toHaveLength(2)
    expect(html, 'inline style attributes are blocked by the policy').not.toMatch(/\sstyle=/)
    expect(html, 'inline event handlers are blocked by the policy').not.toMatch(/\son[a-z]+=/)
    const { response: other } = await page(newId())
    expect(other.headers.get('content-security-policy')).not.toContain(nonce)
  })
})

describe('unknown routes', () => {
  it.each([
    ['/', 'GET'],
    ['/p', 'GET'],
    ['/p/', 'GET'],
    ['/p/{id}/', 'GET'],
    ['/p/{id}/status/', 'GET'],
    ['/p/{id}/photo.jpg/x', 'GET'],
    ['/p/{id}/qr.svg', 'GET'],
    ['/p/not-an-id', 'GET'],
    ['/p/not-an-id/status', 'GET'],
    ['/p/AAAAAAAAAAAAAAAAAAAA..', 'GET'],
    ['/media/{id}/photo.jpg', 'GET'],
    ['/markers/{id}', 'GET'],
    ['/api/captures', 'GET'],
    ['/api/captures/{id}', 'GET'],
    ['/api/captures/{id}/photo.jpg', 'GET'],
    ['/p/{id}', 'POST'],
    ['/p/{id}/photo.jpg', 'DELETE'],
    ['/api/captures/{id}/photo.jpg', 'DELETE'],
  ])('%s with %s is 404 without a listing', async (template, method) => {
    const id = newId()
    await seed(id, ['photo.jpg', 'meta.json'])
    const response = await get(template.replace('{id}', id), { method })
    expect(response.status).toBe(404)
    const body = await response.text()
    expect(body).not.toContain(id)
    expect(body).not.toContain('photo.jpg')
  })

  it('leaves a stored capture untouched by a DELETE', async () => {
    const id = newId()
    await seed(id, ['photo.jpg'])
    await get(`/api/captures/${id}/photo.jpg`, {
      method: 'DELETE',
      headers: { authorization: 'Bearer test-upload-token' },
    })
    expect(await env.SHARES.head(`media/${id}/photo.jpg`)).not.toBeNull()
  })
})
