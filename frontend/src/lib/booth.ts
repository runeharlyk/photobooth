import { readable } from 'svelte/store'

export type Screen = 'idle' | 'countdown' | 'capturing' | 'result'

export interface BoothState {
  screen: Screen
  remaining: number | null
  capture_id: string | null
  delete_available: boolean
  boomerang_ready: boolean
  video_ready: boolean
  message: string | null
  fault: string | null
  share_url: string | null
}

export const booth = readable<BoothState | null>(null, (set) => {
  const source = new EventSource('/api/events')
  source.onmessage = (event) => set(JSON.parse(event.data))
  return () => source.close()
})

const postIgnoringFailure = (path: string): Promise<void> =>
  fetch(path, { method: 'POST' }).then(
    () => undefined,
    () => undefined,
  )

export const tap = () => postIgnoringFailure('/api/tap')
export const deleteCapture = () => postIgnoringFailure('/api/delete')
export const newPhoto = () => postIgnoringFailure('/api/new-photo')
