import { handleUpload } from './upload'

export default {
  async fetch(request, env): Promise<Response> {
    const segments = new URL(request.url).pathname.split('/').slice(1)
    if (request.method === 'PUT' && segments.length === 4 && segments[0] === 'api' && segments[1] === 'captures') {
      return handleUpload(request, env, segments[2], segments[3])
    }
    return notFound()
  },
} satisfies ExportedHandler<Env>

function notFound(): Response {
  return new Response('Not found', { status: 404 })
}
