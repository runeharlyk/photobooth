# Share Worker

The Cloudflare Worker behind the booth's QR links on `https://photo.runeharlyk.dk`.
It accepts the booth's authenticated uploads into the R2 bucket `photobooth-shares` and serves each capture's guest page, status and files.
The interface it implements is fixed in the "Sharing" and "Media and the QR link" sections of `docs/superpowers/specs/2026-10-17-photobooth-app-design.md`.

| Route | Purpose |
| --- | --- |
| `PUT /api/captures/{id}/{name}` | Booth upload of `photo.jpg`, `boomerang.mp4`, `video.mp4` or `meta.json`, with `Authorization: Bearer <UPLOAD_TOKEN>`; 204 on success, 401 without the right token, 400 for a bad id, name, content type, empty body or invalid `meta.json`. |
| `GET /p/{id}` | Guest page: waiting, ready (photo and downloads) or expired. |
| `GET /p/{id}/status` | `{"photo", "boomerang", "video", "expired"}` as JSON, never cached. |
| `GET /p/{id}/{photo.jpg,boomerang.mp4,video.mp4}` | The file; `?download=1` serves it as an attachment. |

Everything else is a 404; `meta.json` is never served and nothing lists captures.

Storage: `media/{id}/{name}` for the files, and `markers/{id}` written once on an id's first upload.
A marker whose media are gone means the capture has expired.

## Test

```sh
pnpm install
pnpm test
pnpm typecheck
```

The tests run inside the Workers runtime through `@cloudflare/vitest-plugin`, against a local Miniflare R2 bucket and the test token set in `vitest.config.ts`; no Cloudflare account is involved.
`pnpm typecheck` first runs `wrangler types`, which writes the git-ignored `worker-configuration.d.ts`.

To try the page by hand, run the Worker locally and upload with curl:

```sh
pnpm exec wrangler dev --local --var UPLOAD_TOKEN:dev-token
curl -X PUT -H "Authorization: Bearer dev-token" -H "Content-Type: image/jpeg" \
  --data-binary @photo.jpg http://127.0.0.1:8787/api/captures/AAAAAAAAAAAAAAAAAAAAAA/photo.jpg
```

## Deploy

Deployment needs the owner's Cloudflare account, on which the zone `runeharlyk.dk` already lives.
Run these from `worker/`:

```sh
pnpm install
pnpm exec wrangler login
pnpm exec wrangler r2 bucket create photobooth-shares --location weur
pnpm exec wrangler r2 bucket lifecycle add photobooth-shares expire-media media/ --expire-days 30
pnpm exec wrangler r2 bucket lifecycle add photobooth-shares expire-markers markers/ --expire-days 365
pnpm exec wrangler r2 bucket lifecycle list photobooth-shares
pnpm exec wrangler secret put UPLOAD_TOKEN
pnpm exec wrangler deploy
```

- `lifecycle add` takes the bucket, a rule name and the key prefix as positionals (`wrangler r2 bucket lifecycle add <bucket> [name] [prefix]`, checked against wrangler 4.147.0); it asks for confirmation unless given `--force`.
- The 30-day rule is what makes photos expire, and the 365-day rule keeps the marker long enough for the page to say "These photos have expired." rather than wait forever; a rule counts from each object's upload, so a re-uploaded file starts again.
- `secret put` prompts for the value; use the same token as `[upload] token` in the booth's local `config.toml`, for example from `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `wrangler deploy` attaches the custom domain `photo.runeharlyk.dk` declared in `wrangler.jsonc` and Cloudflare creates its DNS record and certificate; a Custom Domain cannot be created on a hostname that already has a CNAME record, so delete any such record for `photo` first. The `workers.dev` and preview URLs are disabled.
