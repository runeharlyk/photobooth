import { handleGuest } from './guest'
import { handleUpload } from './upload'

export default {
  async fetch(request, env): Promise<Response> {
    const [root, ...rest] = new URL(request.url).pathname.split('/').slice(1)
    if (root === 'api') {
      const [collection, id, name, ...extra] = rest
      if (request.method === 'PUT' && collection === 'captures' && id !== undefined && name !== undefined && extra.length === 0) {
        return handleUpload(request, env, id, name)
      }
    }
    if (root === 'p') return (await handleGuest(request, env.SHARES, rest)) ?? notFound()
    return notFound()
  },
} satisfies ExportedHandler<Env>

function notFound(): Response {
  return new Response('Not found', { status: 404 })
}
