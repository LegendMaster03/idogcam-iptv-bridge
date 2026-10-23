# iDogCam IPTV Bridge — Gingr V2 prototype (v0.3.1)

An on-demand iDogCam HLS compatibility adapter that exposes a single Jellyfin M3U tuner. The first verified viewer family is Gingr V2. The first configured facility is **DogBeach - Ponte Vedra Beach** (viewer IDs 10088, 10089, and 3782).

## Viewer-provided authorization

The upstream HLS server **does enforce** the `auth` query parameter: the user's live test returned HTTP 200 with the value published by the official viewer, and HTTP 401 when it was omitted or invalid. The three official viewer pages expose the matching authorization value, including the Pool page while it is offline.

**v0.2 removes the captured-HAR credential dependency.** Instead of reading a saved tokenized source URL, the bridge retrieves each authorized viewer page and extracts the viewer's stream parameters. Credentials are held in process memory, refreshed with the viewer metadata (5 minutes by default), and refreshed immediately after a playlist HTTP 401. The M3U playlist and API expose no upstream URL, password, or auth parameter.

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

    python3 tools/import_listing.py --config config.json \
      --facility dogbeach-ponte-vedra --listing-file /path/to/authorized-listing.html

The default is a **read-only preview**. Add `--apply` after reviewing the IDs
to update only `config.json`. Restart the bridge afterward. Existing viewer
IDs are preserved and other facilities are unchanged.

For public listings hosted on the official iDogCam website, use
`--listing-url https://www.idogcam.com/idcviewcams.php` instead. A page with
no direct viewer links will yield zero additions; this does **not** mean there
are no other cameras. Many listings may be rendered dynamically.

**Not yet supported:** automatic full-site discovery, customer account login,
or credential-assisted channel unlocking. No general camera-ID directory or
authorized login API has been independently verified. The official provider
distinguishes password-free group cameras from cameras requiring account
login, check-in, payment, or an assigned password. Do not scan numeric IDs.
Provider-specific login must be verified before accepting customer credentials.
Also, Jellyfin's shared M3U tuner is not a per-customer access-control system.

## Private listing imports and shared Jellyfin tuners

Jellyfin's M3U tuner is shared with all Jellyfin users. New viewers imported
from public or authenticated camera listings are now staged in
`pending_viewer_ids` (not in the active `viewer_ids` list). They are not
published by `/channels.m3u` or accessible through the HLS bridge.

After independently confirming that a camera is appropriate to share with
all users, explicitly publish it:

```bash
python3 tools/promote_public.py --config config.json --facility dogbeach-ponte-vedra --ids 10089 --confirm-public
```

Do not promote customer-specific or reservation-restricted cameras. No
provider login API for ordinary Gingr customers has been verified; entering
an arbitrary password into this bridge would not establish authorization.
Use an authorized saved listing for staging, and never commit credentials,
cookies, or HAR session values. Existing configured cameras are unchanged.

## Scope and limitations

1. Camera discovery is seeded by known viewer IDs or an explicitly provided official listing. A general camera directory API and authenticated customer-portal integration are not yet verified; do not scan or guess IDs. Offline cameras remain listed.
2. Only Gingr V2 extensionless-HLS and same-origin segment URLs have been exercised with local fixtures. Alternate player types, player-side JavaScript encodings, login-only views, and private cameras require separate adapters.
3. The live viewer page must include an unambiguous credential and quality value; the upstream host must be discoverable from the page or configured as a nonsecret `stream_origin`.
4. Dynamic viewer authorization is cached for five minutes, with forced renewal on playlist HTTP 401. The bridge does not implement user login flows or invent missing credentials.
5. Schedule messages are free text. A structured operating-hours API and an offline slate are not implemented.
6. No automatic cross-facility directory enumeration, XMLTV/EPG, media caching, shared HLS-session coordination, or scheduled recording.
7. No live Jellyfin/TrueNAS deployment test has been completed in this build. The existing successful FFmpeg relay test does not establish this rewritten-proxy implementation works with every Jellyfin client.

## Security

Only add camera IDs exposed by a facility's authorized public or customer-facing listing. Keep the bridge on a private Docker network; `/channels.m3u`, `/api/channels`, and `/hls/...` have **no bridge authentication**. Credentials are sent only to permitted upstream `*.viewidogcam.com` origins over HTTPS and are never emitted in the local M3U. Upstream redirects and cross-origin media resources are rejected. Local media URLs are opaque and expire.

The bridge is currently intended for authorized local household viewing, not a public rebroadcast service. Respect the facility's camera access terms.

## Development

```bash
PYTHONPATH=src python3 -m pytest -q
```

Tests use synthetic camera credentials and local HTTP fixtures. They do not contact live viewers or require saved HAR credentials.
