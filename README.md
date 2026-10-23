# iDogCam IPTV Bridge — Gingr V2 prototype (v0.4.0)

An on-demand iDogCam HLS compatibility adapter that exposes a single Jellyfin M3U tuner. The first verified viewer family is Gingr V2. The first configured facility is **DogBeach - Ponte Vedra Beach** (viewer IDs 10088, 10089, and 3782).

## Viewer-provided authorization

The upstream HLS server **does enforce** the `auth` query parameter: the user's live test returned HTTP 200 with the value published by the official viewer, and HTTP 401 when it was omitted or invalid. The three official viewer pages expose the matching authorization value, including the Pool page while it is offline.

**v0.2 and later remove the captured-HAR credential dependency.** Instead of reading a saved tokenized source URL, the bridge retrieves each authorized viewer page and extracts the viewer's stream parameters. Credentials are held in process memory, refreshed with the viewer metadata (5 minutes by default), and refreshed immediately after a playlist HTTP 401. The M3U playlist and API expose no upstream URL, password, or auth parameter.

Authentication is **not bypassed**. The official viewer supplies authorization, and the stream server validates it. This behavior is verified for the user's DogBeach viewer pages; other facilities, private cameras, and other player implementations may require distinct authorized access methods.

## Functionality

- **Stable channels:** `tvg-id` and `tvg-chno` equal the official viewer ID, not the upstream `cameraNum`.
- **Metadata:** Uses official viewer `<title>` for channel names and captures published offline messages. This text is not treated as machine-readable scheduled hours.
- **Facility grouping:** Includes the configured facility name in M3U `group-title`. Jellyfin's UI may not render this as a folder.
- **On-demand HLS:** Rewrites extensionless upstream segment URLs to local `.ts` URLs and proxies the original bytes, without decoding or transcoding.
- **Independent configuration:** Facility and authorized viewer IDs live in `config.json`. Stream credentials are not included, and an optional **nonsecret** `stream_origin` supplies the upstream hostname when the viewer page omits it.
- **Offline channels:** Offline viewers remain listed; requesting video returns HTTP 503. The original offline message is available through `/api/channels`, but informational video slates are not yet implemented.
- **Fail-closed source parsing:** A missing/ambiguous credential, camera number, quality, or origin does not lead the bridge to guess values or borrow credentials from another viewer.

## Endpoints

| URL | Result |
| --- | --- |
| `/healthz` | Bridge process liveness |
| `/channels.m3u` | M3U containing configured cameras |
| `/api/channels` | Viewer IDs, names, camera numbers, offline messages, stream-resolvable flag; **no upstream secrets** |
| `/xmltv.xml` | Eight-day XMLTV guide based on published camera-hour notices, or informational blocks when the schedule cannot be parsed |
| `/hls/{viewer_id}/index.m3u8` | Authorized, rewritten HLS manifest or HTTP 503 |
| `/media/{viewer_id}/{token}.ts` | Proxied HLS segment, with temporary opaque token |

## Local metadata and authorization probe (without Docker)

First copy `examples/config.dogbeach.json` to `config.json`. From the project directory on TrueNAS, execute:

```bash
cp examples/config.dogbeach.json config.json
python3 tools/probe_live.py config.json
```

The probe contacts the **official viewer for each configured ID**, reports its title, current `cameraNum` when present, and whether it resolved a stream from the page. For online cameras it tests the HLS playlist (expected HTTP 200 and `Valid HLS: True`). **It never prints tokenized upstream URLs or auth credentials.** Pool can be listed but unavailable outside its operating hours.

For the initial DogBeach configuration, successful verification should report a resolved source for Sandcastle and Golf while online. If not, do not assume the bridge is ready: the live page's JavaScript may differ from the local fixtures and the extractor may require revision. No external network is available in the prototype-building environment, so the direct viewer-source extraction has **not yet been live-tested** from this version.

## TrueNAS test deployment

**Prerequisites:** a persistent TrueNAS dataset (not `/tmp` or the boot pool), Docker Compose, an existing Jellyfin container on the `ix-jellyfin_default` Docker network, and access to authorized viewer pages. Do not install Python or FFmpeg on the TrueNAS host for the container deployment.

1. Copy the project directory to a persistent dataset; open its directory in a TrueNAS shell.
2. Create a local configuration:

   ```bash
   cp examples/config.dogbeach.json config.json
   ```

   Check and adjust the Docker network and optional `stream_origin` for the relevant facility. The example stream origin is a **hostname only**, not a credential. Only add authorized viewers from an official listing. Do not enumerate numeric IDs.
3. Run `python3 tools/probe_live.py config.json`. If the official page does not produce a valid HLS source for the live cameras, stop here and investigate the page format before deployment.
4. Build and start the adapter, without changing the managed Jellyfin container:

   ```bash
   sudo docker compose up -d --build
   sudo docker compose ps
   ```

5. From the Jellyfin container, test a live camera (the port and path are internal to the existing Jellyfin network):

   ```bash
   sudo docker exec ix-jellyfin-jellyfin-1 /usr/lib/jellyfin-ffmpeg/ffprobe \
     -v error -show_entries stream=codec_name,width,height,r_frame_rate \
     -of json http://idogcam-bridge:8080/hls/10088/index.m3u8
   ```

6. In Jellyfin Dashboard > Live TV > Tuner Devices, add an **M3U Tuner** with `http://idogcam-bridge:8080/channels.m3u`. Test actual video in a Jellyfin client, including channel changes and operation when the camera closes.
7. **Only after** the bridge has proven reliable, remove the former temporary FFmpeg/Nginx test containers, if desired. They are unrelated to this compose project and will not be touched by it.

To remove only the bridge service, run `sudo docker compose down` in its directory. Avoid `docker compose down -v` unless you intend to delete volumes.

### Migration from v0.1

Version 0.1 used a secret file named `secrets/dogbeach-manifest-url` and `schema_version: 1`. Version 0.2 uses `schema_version: 2` and no secret file. Update `config.json` using the new example; do not reuse the old configuration. The Compose configuration no longer mounts `./secrets`. Once cutover is verified, securely remove the old credential file and temporary containers if no longer needed. **Nothing is automatically deleted from TrueNAS.**


## Importing cameras from an official listing (experimental)

Version 0.3 includes a conservative **opt-in listing importer** for Gingr V2 viewers.
It does not enumerate sequential camera IDs, guess private endpoints, or bypass
iDogCam or Gingr authentication. The importer accepts only hyperlinks to official
Gingr V2 viewer pages found in a listing you explicitly supply. It verifies
each candidate's official page title against the selected facility before it
adds anything to the local configuration. Cameras remain listed when offline.

Example, given an **authorized** facility-specific HTML listing you have
already exported (review that file for personal data and credentials):

```bash
python3 tools/import_listing.py --config config.json \
  --facility dogbeach-ponte-vedra --listing-file /path/to/authorized-listing.html
```

The default is a **read-only preview**. Once you have reviewed the listed
IDs, add `--apply` to stage them in `pending_viewer_ids`; the new channels
are deliberately hidden from the shared Jellyfin tuner. The original camera
list is never replaced. See **Protected discovery** below for manual public
promotion.

For public pages hosted on the official iDogCam site, use
`--listing-url https://www.idogcam.com/idcviewcams.php` instead. A page
with no direct viewer hyperlinks will yield zero additions; it does **not**
mean the facility has no other cameras. Some facility/portal listings may be
rendered dynamically and require a separately verified discovery adapter.

**This is not yet automatic site-wide discovery or customer login support.**
The public site lists facilities, but no general viewer-ID directory or
authorized-login API has been verified. The official documentation also
distinguishes password-free iDogCam ACCESS group cameras from Gingr public
cameras that may require account login, a checked-in pet, or a webcam service.
Support for credential-assisted listing discovery must follow a documented or
independently validated authorized sign-in flow, with secrets stored outside
the Git repository and appropriately scoped to Jellyfin users. A facility-wide
M3U tuner does not enforce per-customer access by itself.

## Protected discovery: quarantine before publication

Gingr can show customer-specific, reservation- or purchase-restricted cameras.
A standard Jellyfin M3U tuner is shared with **every Jellyfin user**, so simply
importing an authenticated customer's camera list would disclose those streams
throughout the Jellyfin server. This bridge does **not** implement that.

In v0.3.1, authorized listings supplied through `tools/import_listing.py`
are **staged** in `pending_viewer_ids` instead of being published in `viewer_ids`.
They do not appear in the M3U, API, or live-video routes. The list can include
camera metadata even when offline. There is no automatic promotion.

For cameras independently confirmed to be appropriate for all Jellyfin users,
review the pending IDs in `config.json` and explicitly promote them:

```bash
python3 tools/promote_public.py --config config.json \
  --facility dogbeach-ponte-vedra --ids 10089 --confirm-public
```

This is a **manual access-scope assertion**, not an authorization bypass or a
provider permissions check. Never promote customer-specific or private room
cameras in a shared Jellyfin installation. Promotion changes only local
`config.json`; restart the bridge to apply. Previously configured Dog Beach
cameras remain published and unchanged.

The official Gingr API requires organization administrator-generated API keys,
not ordinary customer login credentials. Its documentation does not establish
a supported pet-parent webcam listing endpoint. We therefore cannot reliably
implement a password-entry form or automatic credential-assisted unlock until
the actual authorized customer portal flow is verified. For now, customers
sign in through Gingr's own interface and may supply a sanitized saved listing
for import; **do not put credentials or session cookies in this repository**.

## Jellyfin programme guide (XMLTV)

Jellyfin requires **two distinct URLs**. The existing M3U tuner stays at
`http://idogcam-bridge:8080/channels.m3u`. In **Dashboard → Live TV → TV Guide
Data Providers**, select **Add → XMLTV** and use:

```text
http://idogcam-bridge:8080/xmltv.xml
```

Save the provider, map the guide channels by the same viewer IDs as the M3U tuner
(`10088`, `10089`, `3782` for Dog Beach), and refresh guide data. The bridge
returns eight days of contiguous programming; no media playback is required to
load these entries.

**Configure each facility's IANA time zone.** For Dog Beach, the example config
includes `"time_zone": "America/New_York"`. Existing `config.json` files must
be updated because the deployment preserves user configuration. When a facility
has no time zone, the bridge deliberately publishes only a schedule-information
block, rather than assigning hours to an incorrect time zone. Restart the
container after editing `config.json`.

The schedule is inferred from the **official viewer's free-text notice**, not
from a provider scheduling API. Recognized single daily intervals and an
explicitly identified break become "Published viewing window" and "Outside
published viewing window" guide blocks. The original wording and any inferred
recurrence are stated in the guide description. These are **not current-camera
online/offline indicators**. Ambiguous announcements remain informational, not
invented schedules. The notice may appear only when a camera is offline.

The bridge retains only the last published *notice text* (never credentials or
upstream URLs) in a private Docker volume named `notice-state`. The notice
expires after 14 days without reconfirmation to avoid retaining stale schedules.
Use `docker compose down` without `-v` to preserve that data. An XMLTV guide
refresh can run while cameras are offline.

## Scope and limitations

1. Camera discovery is seeded by known viewer IDs or an explicitly provided official listing. A general facility-camera directory API and authenticated customer-portal listing integration are not yet verified; do not scan or guess IDs. Offline cameras are retained in the listing.
2. Only Gingr V2 extensionless-HLS and same-origin segment URLs have been exercised with local fixtures. Alternate player types, player-side JavaScript encodings, login-only views, and private cameras require separate adapters.
3. The live viewer page must include an unambiguous credential and quality value; the upstream host must be discoverable from the page or configured as a nonsecret `stream_origin`.
4. Dynamic viewer authorization is cached for five minutes, with forced renewal on playlist HTTP 401. The bridge does not implement user login flows or invent missing credentials.
5. Schedule messages are free text. XMLTV guide windows are inferred conservatively from those notices; they do not verify live availability. A structured operating-hours API and an offline video slate are not implemented.
6. No automatic cross-facility directory enumeration, structured provider schedule API, media caching, shared HLS-session coordination, or scheduled recording.
7. No live Jellyfin/TrueNAS deployment test has been completed in this build. The existing successful FFmpeg relay test does not establish this rewritten-proxy implementation works with every Jellyfin client.

## Security

Only add camera IDs exposed by a facility's authorized public or customer-facing listing. Keep the bridge on a private Docker network; `/channels.m3u`, `/api/channels`, and `/hls/...` have **no bridge authentication**. Credentials are sent only to permitted upstream `*.viewidogcam.com` origins over HTTPS and are never emitted in the local M3U. Upstream redirects and cross-origin media resources are rejected. Local media URLs are opaque and expire.

The bridge is currently intended for authorized local household viewing, not a public rebroadcast service. Respect the facility's camera access terms.

## Development

```bash
PYTHONPATH=src python3 -m pytest -q
```

Tests use synthetic camera credentials and local HTTP fixtures. They do not contact live viewers or require saved HAR credentials.
