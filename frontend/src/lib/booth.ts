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
  version: string
}

/** The latest booth state, or null while the backend is unreachable. */
export const booth = readable<BoothState | null>(null, (set) => {
  const source = new EventSource('/api/events')
  let pageVersion: string | null = null
  source.onmessage = (event) => {
    try {
      const state: BoothState = JSON.parse(event.data)
      pageVersion ??= state.version
      if (state.version !== pageVersion) {
        location.reload()
        return
      }
      set(state)
    } catch (error) {
      console.warn('Ignoring malformed state message', error)
    }
  }
  source.onerror = () => set(null)
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
