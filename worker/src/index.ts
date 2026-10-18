export default {
  async fetch(): Promise<Response> {
    return new Response('Not found', { status: 404 })
  },
} satisfies ExportedHandler<Env>
