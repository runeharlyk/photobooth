# Sharing contract: booth <-> Cloudflare Worker

The "Sharing", "Media and the QR link" and "Events" sections of 2026-10-17-photobooth-app-design.md are the binding source; this file fixes the interface between the booth uploader (backend/) and the share Worker (worker/).

## Identifiers
- `id`: the capture id, `[A-Za-z0-9_-]{22}` (secrets.token_urlsafe(16)). Anything else is rejected with 404 on guest routes and 400 on the upload route.
- `name`: one of `photo.jpg`, `boomerang.mp4`, `video.mp4`, `meta.json`.

## Upload API (booth -> Worker)
`PUT https://photo.runeharlyk.dk/api/captures/{id}/{name}`
- Header `Authorization: Bearer <token>`; the Worker compares in constant time with its secret `UPLOAD_TOKEN`. Missing or wrong token: 401, nothing stored.
- `Content-Type`: `image/jpeg`, `video/mp4` or `application/json` matching the name; otherwise 400.
- Body: the file bytes (photo up to ~10 MB, videos a few MB; the Cloudflare request body limit is 100 MB).
- Success: 204. Re-uploading the same name overwrites (idempotent), so a retried upload is safe.
- `meta.json` body: `{"captured_at": "<ISO 8601 UTC>", "event": "<event>"}`; the Worker validates both fields (event matches `[a-z0-9][a-z0-9-]{0,39}`) and otherwise returns 400; unknown extra keys are ignored, so the booth may add fields later without breaking uploads.
- The booth uploads in this order: `photo.jpg` first, then `meta.json`, then `boomerang.mp4` and `video.mp4` when they exist (they may be built later; each is uploaded once it exists).

## Storage layout (R2 bucket `photobooth-shares`, binding `SHARES`)
- `media/{id}/{name}` for the four files, stored with their content type.
- `markers/{id}`: written by the Worker on the first successful upload of an id, body `{"uploaded_at": "<ISO 8601 UTC>"}`; never overwritten.
- Lifecycle rules (configured at deploy): prefix `media/` expires after 30 days; prefix `markers/` after 365 days. A marker without media therefore means "expired".

## Guest routes (public)
- `GET /p/{id}`: HTML page, mobile-first, the booth's look (black background, green `#2bd96b`, the tilted black sticker label with green capitals and a hard shadow; fonts self-hosted or system fonts, no third-party requests).
  - No marker and no photo: "Getting your photos ready..." and the page polls `/p/{id}/status` every 3 s, updating in place when files arrive; polling stops after 10 minutes in any state and the page then offers "Reload this page to check again." (free-tier cost).
  - Photo present: shows the photo, and download buttons for Photo, Boomerang and Video, each enabled once that file exists (the boomerang and video may arrive later; keep polling until all three exist or 10 minutes have passed).
  - Marker present but no media: "These photos have expired."
- `GET /p/{id}/status`: JSON `{"photo": bool, "boomerang": bool, "video": bool, "expired": bool}`, `Cache-Control: no-store`. Guest routes use only object reads (head/get, Class B); no anonymous request may cause a list or write. `expired` means a marker exists and the photo does not (the photo is always uploaded first).
- `GET /p/{id}/{photo.jpg|boomerang.mp4|video.mp4}`: the file with its content type; `?download=1` adds `Content-Disposition: attachment; filename="photobooth-{id-prefix}-{photo|boomerang|video}.{ext}"`. 404 if absent. `meta.json` is never served publicly.
- Anything else: 404 with no listing; no route lists captures.

## Booth configuration (local file only, never accepted from the remote settings document)
```toml
[upload]
url = "https://photo.runeharlyk.dk"
token = "<secret>"
```
Absent `[upload]` table: uploading is disabled (development, tests). The token is never logged.
