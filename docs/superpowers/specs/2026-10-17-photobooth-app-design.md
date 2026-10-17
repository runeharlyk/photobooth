# Photobooth app - design

## Intent

A permanent photobooth on the wall of a shared dorm kitchen, which also comes off the wall for parties.
Someone taps one button, poses during a short countdown, and gets a photo, a boomerang and a short video from that one capture through a QR code.
Every photo nobody deletes also goes to a shared album that the kitchen TV's Chromecast shows as its screensaver.

The booth software is written from scratch rather than built on pibooth or photobooth-app.
Both were run on the booth on 2026-10-16: pibooth's interface can only be recoloured, and photobooth-app themes only through CSS, so neither can drop the choice between photo, video and boomerang, produce all three from one capture, or show the screens below.
The reference points are Myselfie (a Danish rental booth: one button, a 3 s countdown, the photo with a QR code, Delete and New photo) and Lightbooth (a Copenhagen wall booth whose video is recorded in the moments before the photo and ends on the photo under a fading white flash).

The background, prices and hardware reasoning live in the owner's Life repository, `projects/ideas/photobooth.md`; this document is what the code must do, and it replaces the 2026-10-08 kitchen design.

## Goals

- One tap gives a photo, a boomerang and a video, with no choice of mode.
- The three screens below, in one consistent visual language.
- The QR code appears the instant the result screen does, whatever the network is doing.
- The same program runs on the development PC (Windows, USB webcam) and on the booth (Raspberry Pi 4B, Camera Module 3), with the difference confined to the camera layer.
- The camera is off whenever nobody is using the booth.

## Non-goals for this version

- Uploading anywhere: the Google Photos album for the TV and the Cloudflare Worker behind the QR link are later milestones. This version stores media locally and writes the QR link it will later serve; the storage layout and a pending-upload marker are designed so the uploader can be added without touching capture.
- Printing, collages, filters, a gallery, a slideshow.
- The LED strip, the lens-cover switch and the enclosure.
- Kiosk autostart and the read-only file system on the Pi; they belong to the Pi milestone after this one.

## Anti-goals

- A live camera image, or any capture, before someone taps.
- Any screen that lets a passer-by browse earlier photos.
- Stock component-library styling mixed with custom styling.

## Constraints

- **Booth hardware:** Raspberry Pi 4B, Raspberry Pi OS (Legacy, 64-bit) Bookworm with desktop, Camera Module 3 (IMX708, standard lens) through picamera2 from apt, Waveshare 10.1inch DSI LCD (C), 1280 x 800 landscape, capacitive touch. The UI runs in Chromium in kiosk mode, full screen.
- **Pi 4 camera behaviour, measured:** the low-resolution stream shares the main stream's crop, so a liveview whose aspect ratio differs from the main stream is stretched. Keep every stream 16:9; the preview is shown letterboxed on the 16:10 screen with thin black bands top and bottom, which the owner accepted.
- **Development PC:** Windows 11, an Innomaker U20Cam-1080p USB camera reached through DirectShow; the PC also exposes several virtual cameras (Pixel, Quest, OBS), so the camera is selected by name from configuration.
- **Tooling:** Python run with uv; the frontend built with pnpm. The owner's convention is English for all code, comments and docs, and no dead code.
- **Offline:** the booth must capture, show results and generate QR codes without internet.

## Screens

All text is English. One look throughout: a black background with scattered green confetti, one green (placeholder `#2bd96b`), and the main label of each screen as a "slapped-on sticker": a black rectangle with green capitals, a green outline and a hard drop shadow, rotated a few degrees and laid across the element it labels. No other button style exists.

**Idle.** A large round green button in the centre with "TAP HERE" as the sticker across it, and "Take a photo" in small grey text below. The whole circle including the sticker is the touch target; nothing else on the screen reacts to touch. The camera is off.

**Countdown.** A tap starts a 3 s countdown. The mirrored live preview fills the screen, letterboxed. The green button travels from the centre to the bottom-right corner and shows 3, 2, 1 there, so faces stay clear. The countdown is drawn over the screen only and never appears in recorded media. At zero the screen flashes white and the photo is taken.

**Result.** The photo in a white print frame, tilted about 2 degrees, on the left; the boomerang plays in that frame as soon as it exists, the still until then. On the right, the QR code under a "SCAN TO GET YOUR PHOTO" sticker, and below it two buttons: Delete (quiet, outlined) and New photo (green). No notice about where photos go.
Delete is available for 15 s after the shot, the owner's choice: the Photos Library API cannot delete an uploaded item, so a photo may only be uploaded once Delete is gone, and 15 s lets a guest who scans at once get the link filled within seconds rather than after the whole result screen.
When the 15 s pass, the Delete button disappears and the capture becomes kept; the result screen stays until its timeout (placeholder 45 s) or New photo, then returns to idle.
Delete removes the photo, boomerang and video and returns to idle. New photo makes the current capture kept at once and starts a new countdown.

**Starting.** While the page has no connection to the backend, before the first state arrives or after the connection drops, it shows the confetti and "Starting up..." in the grey helper style, with no button and nothing from an earlier capture; it returns to the current screen by itself once the backend answers.

## Capture

1. The tap turns the camera on and starts the live preview.
2. From about 1 s into the countdown until the shot, the preview frames are recorded.
3. At zero the full-resolution photo is taken. On the Pi this switches the camera to its still configuration; any gap that causes falls between the end of the clip and the photo, where it is not seen.
4. The camera turns off again when the booth returns to idle, or after the result-screen timeout at the latest.
5. In the background, from the clip and the photo:
   - **Boomerang:** the last second or so of the clip, played forwards then backwards, looping, no flash.
   - **Video:** the clip, followed by the photo held for about 2 s under a white overlay that starts at about 95 % opacity and fades to 0.

Timings (countdown 3 s, recording start 1 s, flash 2 s and 95 %, boomerang length, delete window 15 s, result timeout 45 s) are configuration, with these defaults.

## Media and the QR link

Each capture gets a random, unguessable id at the moment of the shot. Its files live together under one directory per capture: the photo, the clip, the boomerang and the video, plus a small metadata file holding the id, the capture time, its state and an `uploaded` marker the later uploader will use.
The clip is kept only so the boomerang and video can be re-exported if the way they are made changes: clips stay while more than 5 GB is free, and below that the oldest clips are deleted first, never the photo, boomerang or video, and never a clip whose media are not yet built (the owner's rule).
The state is `pending` while Delete is available and `kept` afterwards, written by the result flow, so the later uploader takes only `kept` captures and never needs to know about screens. A capture found `pending` at startup, after a crash or power cut, becomes `kept`, since the rule is keep unless deleted (designer's call).

The QR code encodes `{share_base_url}/{id}`, built locally the instant the result screen opens; `share_base_url` is configuration. The Cloudflare Worker that will answer that URL, showing "getting your photos ready" until the uploads arrive, is a later milestone. Until it exists the link does not resolve; that is accepted for this version.

## Failures

The booth is unattended, so a failure must be visible on the screen rather than only in a log, without a second visual style (designer's call); the Starting view covers the backend being unreachable.
- A capture that fails (camera does not open, the photo is not written) returns to idle with a one-line message in the small grey helper-text style in place of "Take a photo", such as "Something went wrong - try again", for a few seconds.
- A boomerang or video that fails to build leaves the photo and the QR link intact; the failure is logged and the result screen keeps showing the still.
- A lasting fault (no camera at startup, media directory not writable or full) replaces "Take a photo" on the idle screen with a short grey line naming it, and the button stays inert.

## Power cuts

The booth loses power without warning, and the Pi will run with a read-only root file system and media on a separate writable partition.
A capture's directory is written atomically (files into a temporary directory, metadata last, then a rename), and startup removes any half-written capture, so a power cut leaves either a complete capture or nothing (designer's call, carried over from the earlier design's requirement that ten mains disconnects lose no queued photo).

## Settings

A configuration file holds the camera source (picamera2, or a webcam by name), the timings above, `share_base_url` and the media directory. An on-screen admin page and its hidden entry on the idle screen are left for later, since an entry with nothing behind it would be dead code.

## How it is built

- **Backend:** Python, run with uv. It owns the camera, the capture sequence, media building and the files, and serves the frontend and a small HTTP API to it.
- **Camera layer:** one interface with two implementations, picamera2 on the Pi and a webcam (via OpenCV or ffmpeg) on the PC. Everything above it is identical on both machines.
- **Frontend:** Svelte with Vite and TypeScript, built with pnpm, served as static files by the backend. Fonts and assets are bundled, since the booth may be offline.
- **Media building:** ffmpeg.

Left to the builder: the web framework, how the preview reaches the browser (MJPEG over HTTP is the obvious choice), how frames are recorded, the video codec and sizes, file formats, the API shape, how the frontend learns of state changes, and the test approach, provided the capture sequence, the media building and the delete path are covered by automated tests that run on the PC without a camera.

## Done means

On the development PC, with the U20Cam: idle, countdown, capture and result run end to end in a browser at 1280 x 800; the photo, boomerang and video are produced and correct; Delete removes all of a capture's files; the QR code appears with the result screen; and the camera is off at idle. The automated tests pass. The Pi run is the next milestone, and the design must not need to change for it.
