# Kitchen photobooth - design

## Goal

A permanent, unattended photobooth on the wall of the dorm's shared kitchen.
Residents take a photo or a four-shot strip, take it with them by QR code, and can opt it into the kitchen TV's photo rotation.
There is no deadline; the first version is the smallest booth that survives a week unattended.

## Success criteria

- It runs for a week after installation without anyone touching it.
- The Google login still works on day 8, which proves the OAuth client is not in Testing mode.
- The TV album holds only photos whose subjects opted in.
- Ten mains disconnects mid-use leave it booting back into the booth every time, with no queued photo lost.

## Constraints

- **Privacy.** The camera is pointed into a room people live in. It is powered only between a tap and the end of a session, never shows a live preview while idle, and nothing reaches the TV without an explicit choice on screen. Kitchen residents agree to the booth before it goes up.
- **Unattended.** Every screen times out back to idle. Failures must be visible on the idle screen, because nobody reads logs.
- **Kitchen.** Steam, grease, knocks, spills, cleaning spray and power cuts.

## Decisions

| Decision | Chosen | Rejected and why |
|---|---|---|
| Output | Digital: QR download and the kitchen TV | Printing: consumables someone pays for, and jams nobody sees |
| TV display | The existing Chromecast 4K's ambient screensaver, signed into the common kitchen account | A dedicated screen: redundant, and casting would take over the TV |
| Platform | Raspberry Pi 3 (owned, unused) | Old Android phone: lithium cell on permanent charge, poor kiosk lockdown. ESP32-P4: OAuth, upload and camera pipeline are each a sub-project |
| Software | pibooth plus one custom plugin | photobooth-app: "Raspberry Pi 3 could work but might be slow", wants 1024 px width. PhotoboothProject: PHP, no LEDs, no cloud |
| Camera | Camera Module 3, standard 75 degree lens | Wide lens and the owned U20Cam USB cameras: 120-130 degrees distorts faces at arm's length. USB webcam: worse image for more money |
| Light | RGBW addressable strip (SK6812 RGBW) behind a diffuser around the screen | Plain RGB: mixed white renders skin poorly |
| Private photos | Kept photos upload to the kitchen account's library, and the screen says so | Local-only download (fallback if a resident objects); separate cloud storage (another service to maintain) |
| Power-loss safety | Read-only overlay root file system plus a writable data partition | 18650 UPS: lithium cells charging unattended in a shared kitchen to protect only unsent photos |
| Mounting | Printed cleat plate screwed to the wall, one locking screw at the bottom | Counter standing: spills and knocks |

## Hardware

| Part | Status |
|---|---|
| Raspberry Pi 3 | Owned |
| Camera Module 3, standard lens | To buy, from USD 25 |
| Touchscreen | To buy; size open, see below |
| SK6812 RGBW strip, 5 V | To buy; length set by the screen's perimeter |
| 74AHCT125 level shifter | To buy; the strip's data line needs 5 V logic |
| 5 V brick-style certified supply | To buy; rated for Pi, display and strip at full white, with a fuse on the LED line |
| Heatsink for the Pi 3 | To buy |
| Enclosure | Printed in PETG on the owned printers (220 x 220 mm bed) |

**Screen size is open.**
The official 7-inch Touch Display 2 (720 x 1280, USD 60) runs from one ribbon cable and the Pi's power, and its frame prints in one piece.
A third-party 10.1-inch HDMI panel (1280 x 800, about EUR 115-122) shows groups at a usable size, but needs HDMI, USB touch and its own power inside the case, and its frame prints in two parts.
The official 10-inch Touch Display 2 requires a Pi 5 and is out.
Both candidates have about the same pixel count, so the choice does not change the Pi's load.

**Fallback if the Pi 3 is too slow** (gate in step 3 of the build order): first lower the preview resolution and frame rate; then a Pi 4, which keeps the 15-pin camera and display connectors.

## Booth flow

1. **Idle.** Tap prompt over a soft LED glow. Camera off. A health line appears only when something is wrong (see Error handling).
2. **Choose.** One photo or a four-shot strip.
3. **Countdown.** Mirrored live preview, LEDs chase around the frame.
4. **Capture.** LEDs go full white for each shot.
5. **Review.** Three buttons:
   - **Delete:** the photo is erased and never leaves the Pi.
   - **Keep:** queued for upload; a QR code is shown for about 60 seconds.
   - **Keep and show on TV:** the same, and the photo is also added to the booth album.
6. Any timeout returns to idle. An abandoned review screen deletes the photo.

The review screen states that kept photos are saved to the kitchen account.

## Software architecture

pibooth (2.1.1 or later) runs the camera, the countdown and the screens.
One plugin package, `pibooth_kitchen`, adds everything else, split into units that each have one job:

| Unit | Responsibility | Depends on |
|---|---|---|
| `review` | The Delete / Keep / Keep and show on TV screen and its timeout; hands a decision to `queue` | pibooth hooks, `queue` |
| `queue` | Persists a kept photo plus a sidecar (`id`, `tv`, `created`) on the data partition; lists, claims and removes entries | File system only |
| `photos` | Google Photos client: refresh the access token, upload bytes, `batchCreate` with or without the album, create the album once | HTTP, stored credentials |
| `uploader` | Background thread draining `queue` through `photos`, with exponential backoff; publishes each photo's result (`baseUrl` or failure) | `queue`, `photos` |
| `qr` | Renders the download URL for the review screen | `uploader` result |
| `leds` | Strip states: idle, countdown, flash, off | rpi_ws281x |
| `health` | Derives the idle screen's warning from token state and queue age | `photos`, `queue` |

`queue` and `review` know nothing about Google, and `photos` knows nothing about pibooth, so each can be tested without the others.
The exact pibooth hook names the plugin attaches to are verified against the 2.1.1 source before implementation, not assumed.

Python environment: picamera2 comes from Raspberry Pi OS (`python3-picamera2`), so the virtual environment is made with `uv venv --system-site-packages` and pibooth is installed into it with uv.

## Google Photos integration

- **Account.** The common kitchen Google account, which the Chromecast already uses.
- **OAuth.** A Google Cloud project owned by Rune with the Photos Library API enabled and a desktop OAuth client. Scope: `photoslibrary.appendonly`. The consent screen is set to **In production** before the first consent, because Testing mode issues refresh tokens that expire after 7 days. Consent is given once on a PC; the refresh token is copied to the booth's data partition.
- **Album.** The app creates its own album on first run and stores its ID, because `batchCreate` can add only to albums the app created; the existing shared kitchen album cannot be targeted. The Chromecast's ambient screensaver is then set to show this album alongside the existing one.
- **Upload.** Bytes to the uploads endpoint for an upload token, then `mediaItems.batchCreate`, passing `albumId` only for TV photos. Every upload also lands in the kitchen account's library; whether the Library API can delete it again is unverified and is checked in build step 2.
- **QR link.** `batchCreate` returns the media item, whose `baseUrl` is active for 60 minutes; the QR code encodes `baseUrl` + `=d`.

## Storage layout

The root file system is read-only via raspi-config's overlay file system.
A separate ext4 partition mounted at `/data` holds everything that must survive a reboot:

```
/data/queue/<id>.jpg
/data/queue/<id>.json      # {"id", "tv", "created"}
/data/state/token.json     # OAuth refresh token
/data/state/album.json     # booth album ID
```

A photo is written to `<id>.jpg.tmp`, synced, then renamed, and the sidecar is written last, so a power cut leaves either a complete entry or a stray `.tmp` that startup deletes.

## Error handling

| Failure | Behaviour |
|---|---|
| No internet | Review screen says "Saved, will upload later" and shows no QR code; `uploader` retries with backoff |
| Refresh token rejected | Uploads pause, the queue keeps photos, the idle screen shows "Booth needs login - tell Rune" |
| Queue older than 24 hours | Idle screen warning |
| `baseUrl` not reachable without login | QR code points to the booth's local HTTP server instead (see Open questions) |
| Camera or display failure | pibooth's failsafe state; systemd restarts the service |
| Power cut | Boots straight into pibooth via systemd; the queue resumes |
| CPU overheating | Logged during the bench test; vents or a lower preview rate fix it before installation |

## LEDs

SK6812 RGBW on a dedicated 5 V supply, data from GPIO18 through the 74AHCT125, driven by `rpi_ws281x` in RGBW mode.
Ground is shared between the supply and the Pi.
States: idle glow (low, warm), countdown chase, full white during capture, off when the screen sleeps.
Full-white brightness is capped in software to what the supply is rated for.

## Enclosure

Front frame and back box in PETG: PLA softens around 55-60 C, and PETG tolerates cleaning spray.
Screen flush, camera centred above it, lens at roughly 150-160 cm, the strip around the screen behind a diffusing rim.
Passive vents at the sides or back, away from the front.
Screws, not snap-fits; the SD card is unreachable without opening the case; maintenance is over SSH.
The case hangs on a printed cleat plate screwed to the wall, with one screw at the bottom against lifting.
Placed away from the stove and kettle, with no window behind the people being photographed.

## Build order

Each step settles the largest remaining risk as cheaply as possible.

1. **Consent.** The kitchen residents agree.
2. **Google side, from a PC, no hardware.** Create the project, set it In production, consent, create the album, upload one photo by script. Scan the `baseUrl` QR code on a phone not signed in to the kitchen account. Add the album to the Chromecast and confirm both albums rotate.
3. **Pi 3 bench test.** Raspberry Pi OS Bookworm (pibooth has not validated Trixie), Camera Module 3, the screen, stock pibooth. Measure preview frame rate and CPU temperature over 30 minutes. **Gate:** acceptable, tune preview, or move to a Pi 4.
4. **Plugin, test-first on a PC.** `queue`, `review` decisions, `uploader` and `health` against a fake `photos`; then one real end-to-end run on the Pi.
5. **LEDs on the bench.**
6. **Enclosure.** Fit-check prints, then the final print in PETG.
7. **Hardening.** Overlay file system, `/data` partition, systemd autostart, ten mains disconnects mid-use, a 48-hour unattended run.
8. **Installation and one week of observation** against the success criteria.

## Testing

- **Unit, on a PC:** `queue` (atomic write, crash leftovers, ordering), `review` (every button and the timeout path, including abandonment deleting the photo), `uploader` (backoff, album only when `tv` is true, a rejected token pausing rather than dropping), `health` (warning thresholds). `photos` is replaced by a fake that can fail on demand.
- **Integration, by hand, once:** one real upload of each kind against the kitchen account, checking album membership in the Google Photos app.
- **Hardware acceptance:** the power-cut, soak and day-8 checks above.

## Open questions

- Screen size: 7-inch official or 10.1-inch HDMI. Decided before the enclosure is designed.
- Whether a `baseUrl` opens without authentication on a phone. The Library API guide states no requirement either way. Settled in build step 2.
- Whether the Chromecast's ambient mode shows two albums at once. The help page implies it. Settled in build step 2.
- Whether the Pi 3 previews smoothly. Settled in build step 3.
- Whether the dorm Wi-Fi lets phones reach the Pi. Matters only for the QR fallback and the private-download fallback.
- pibooth's exact hook names and its review-screen extension point in 2.1.1.

## Out of scope

Printing, GIFs and boomerangs, filters and backgrounds, per-person albums, and hosting private photos anywhere other than the kitchen account.
