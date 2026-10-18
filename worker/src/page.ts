import type { PublicFile, Status } from './guest'

const DOWNLOADS: { name: PublicFile; key: 'photo' | 'boomerang' | 'video'; label: string }[] = [
  { name: 'photo.jpg', key: 'photo', label: 'Photo' },
  { name: 'boomerang.mp4', key: 'boomerang', label: 'Boomerang' },
  { name: 'video.mp4', key: 'video', label: 'Video' },
]

const CONFETTI = [
  [8, 12, 30],
  [20, 72, -20],
  [35, 20, 60],
  [66, 14, 10],
  [80, 66, -45],
  [90, 30, 80],
  [12, 46, 15],
  [70, 84, 40],
  [52, 90, -65],
  [4, 86, 50],
  [94, 50, 10],
  [46, 6, -30],
]

const CONFETTI_STYLE = CONFETTI.map(
  ([left, top, angle], index) =>
    `.confetti span:nth-child(${index + 1}) { left: ${left}%; top: ${top}%; rotate: ${angle}deg; }`,
).join('\n')

const STYLE = `
:root { --green: #2bd96b; --green-rim: #1fa652; --muted: #bbb; }
*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: #000; color: #fff; }
body { font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; }
[hidden] { display: none !important; }
.confetti { position: fixed; inset: 0; opacity: 0.5; pointer-events: none; }
.confetti span { position: absolute; width: 24px; height: 8px; border-radius: 3px; background: var(--green); }
main { position: relative; width: 100%; max-width: 480px; min-height: 100vh; min-height: 100svh;
  padding: 48px 16px calc(32px + env(safe-area-inset-bottom));
  display: flex; flex-direction: column; justify-content: center; gap: 24px; }
section { display: flex; flex-direction: column; align-items: center; gap: 36px; }
.sticker { display: inline-block; padding: 0.2em 0.6em; border: 4px solid var(--green); background: #111;
  box-shadow: 5px 7px 0 #0008; color: var(--green); font-family: 'Archivo Black', 'Arial Black', system-ui, sans-serif;
  font-size: 24px; font-weight: 900; letter-spacing: 0.03em; line-height: 1.2; text-align: center;
  text-transform: uppercase; rotate: -4deg; margin: 0; }
.circle { display: grid; place-items: center; width: min(72vw, 300px); aspect-ratio: 1; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 80px #2bd96b66, inset 0 -13px 0 var(--green-rim); }
.circle .sticker { max-width: 115%; }
.circle.gone { background: #333; box-shadow: inset 0 -13px 0 #222; }
.circle.gone .sticker { rotate: 3deg; }
@media (prefers-reduced-motion: no-preference) {
  .circle.busy { animation: breathe 1.4s ease-in-out infinite alternate; }
  @keyframes breathe { to { scale: 0.94; } }
}
.print { position: relative; width: 100%; margin-top: 18px; rotate: -2deg; border: 10px solid #fff; background: #111;
  box-shadow: 10px 14px 0 #0009; }
.print img { display: block; width: 100%; min-height: 120px; }
.print .sticker { position: absolute; top: -26px; left: 50%; translate: -50% 0; rotate: -5deg; white-space: nowrap; }
.downloads { display: grid; gap: 14px; width: 100%; }
.button { display: block; padding: 16px 0; border-radius: 999px; background: var(--green); box-shadow: 0 0 28px #2bd96b55;
  color: #000; font-size: 20px; font-weight: 800; text-align: center; text-decoration: none; }
.button:active { scale: 0.97; }
.button[aria-disabled='true'] { padding: 13px 0; border: 3px solid #555; background: none; box-shadow: none; color: var(--muted); }
.helper { margin: 0; color: var(--muted); font-size: 16px; text-align: center; }
`

const SCRIPT = `
(() => {
  const main = document.querySelector('main')
  const base = main.dataset.base
  const deadline = Date.now() + 10 * 60 * 1000
  const show = (name) => {
    for (const section of main.querySelectorAll('section')) section.hidden = section.id !== name
  }
  const apply = (status) => {
    if (status.expired) {
      show('expired')
      return true
    }
    if (!status.photo) return false
    show('ready')
    const photo = main.querySelector('#ready img')
    if (!photo.hasAttribute('src')) photo.src = base + 'photo.jpg'
    for (const link of main.querySelectorAll('a[data-name]')) {
      if (status[link.dataset.key] && !link.hasAttribute('href')) {
        link.href = base + link.dataset.name + '?download=1'
        link.removeAttribute('aria-disabled')
      }
    }
    return status.boomerang && status.video
  }
  const poll = async () => {
    if (Date.now() > deadline) {
      document.getElementById('hint').hidden = false
      return
    }
    if (!document.hidden) {
      try {
        const response = await fetch(main.dataset.poll, { cache: 'no-store' })
        if (response.ok && apply(await response.json())) return
      } catch {}
    }
    setTimeout(poll, 3000)
  }
  if (!apply(JSON.parse(main.dataset.status))) setTimeout(poll, 3000)
})()
`

export function renderPage(id: string, status: Status, nonce: string): string {
  const visible = status.expired ? 'expired' : status.photo ? 'ready' : 'waiting'
  const hidden = (section: string) => (section === visible ? '' : ' hidden')
  const photoSource = status.photo ? ` src="/p/${id}/photo.jpg"` : ''
  const downloads = DOWNLOADS.map(({ name, key, label }) => {
    const state = status[key] ? `href="/p/${id}/${name}?download=1"` : 'aria-disabled="true"'
    return `<a class="button" data-name="${name}" data-key="${key}" ${state}>${label}</a>`
  }).join('\n      ')

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="robots" content="noindex">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#000000">
  <title>Your photobooth photos</title>
  <style nonce="${nonce}">${STYLE}${CONFETTI_STYLE}</style>
</head>
<body>
  <div class="confetti" aria-hidden="true">${'<span></span>'.repeat(CONFETTI.length)}</div>
  <main data-base="/p/${id}/" data-poll="/p/${id}/status" data-status="${escapeAttribute(JSON.stringify(status))}">
    <section id="waiting"${hidden('waiting')}>
      <div class="circle busy"><p class="sticker">Getting your photos ready...</p></div>
    </section>
    <section id="ready"${hidden('ready')}>
      <div class="print"><span class="sticker">Your photo</span><img${photoSource} alt="Your photobooth photo"></div>
      <div class="downloads">
      ${downloads}
      </div>
    </section>
    <section id="expired"${hidden('expired')}>
      <div class="circle gone"><p class="sticker">These photos have expired.</p></div>
    </section>
    <p class="helper" id="hint" hidden>Reload this page to check again.</p>
  </main>
  <script nonce="${nonce}">${SCRIPT}</script>
</body>
</html>
`
}

function escapeAttribute(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('"', '&quot;')
}
