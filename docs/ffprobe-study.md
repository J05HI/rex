# What a source's probe decides: the ffprobe study (2026-11-05/06)

A source must send each file's ffprobe output (`media_info`) before rex shows it (`docs/feed-api.md`,
"Held until probed"; findings, "An item without a probe cannot play"). This study asks what the Plex
apps do with that probe once it is there: which fields change what a person sees, what plays, what
converts, and what breaks when a field is missing or false. It was measured by pushing the same real
files many times, each copy with a different ffprobe, and playing every copy in the official Plex app
and in Plezy on a phone.

Everything below was observed unless it says otherwise. "Not tested" marks what was reasoned, not seen.

## Summary

- **Plex for Android decides direct play on the phone, from the probe alone.** It never looks at the
  file first. A wrong codec name converts a playable file or skips a conversion a device needed. Labels
  (resolution, HDR, bitrate, container) changed nothing on a Pixel at Original quality.
- **Plezy (mpv) ignores the probe for playback.** It asks for no decision, reads the file, and lists the
  file's own tracks; the probe only fills its details page and the timeline it reports.
- **The file size is the dangerous field in proxy mode.** rex answers a part read with the probe's size,
  not the length its source serves: a size too large hangs Plezy and makes the Plex app re-encode, one too
  small cuts the file short and marks it watched in Plezy. Under `--part-mode redirect` the client reads
  the source itself and the size stops mattering for direct play.
- **The duration decides watch state.** A duration too short makes rex refuse every progress report past
  it (no resume point), none at all leaves no resume point, one too long means the title never counts as
  watched.
- **Track picks go by the probe's stream index**, in the Plex app and in rex's conversions alike. A probe
  whose indexes are right but whose descriptions are wrong plays the wrong language with no error; rex's
  own default pick follows the probe's languages and flags.
- **Send ffprobe's whole output, or only what is known.** A guessed codec, size, duration or track order
  did more harm than a missing field.

## Setup

| | |
|---|---|
| Server | rex nightly `2026.11.05.0354`, a lab server on the test host (zen), `--gdm off`, `REX_TRANSCODER` set (jellyfin-ffmpeg 8.1, VAAPI on the host's Intel GPU). Proxy mode unless a section says redirect |
| Account | The lab server was claimed to the owner's Plex account for the phone tests (the phone's apps were signed in there), the plex.tv device deleted and `claim_state.json` removed afterwards. `DELETE /myplex/account` is no unclaim on rex (below) |
| Phone | Pixel 6 (`oriole`), Android 16, rooted; Plex for Android `2026.19.1`; Plezy `2.24.0` |
| Bytes | Part 1: `python3 -m http.server` (no `Range`). The track pass and the redirect runs: `probes/probe-study/cdn.py`, a server with `Range` that logs every read, behind a loopback address that answers each GET with a 302 to it, as a debrid provider's link does |
| Items | Each probe variant is its own item (`id`/`path` a symlink to the real file), unplaced, named by `display` (`A00 truth: H.264/AAC MP4`), so rex identifies nothing and each copy keeps its own probe. A file keeps the streams of the probe it was first shown with, so a variant is a new id, never a re-push |

The files (`probes/probe-study/make_media.sh`):

| File | Content |
|---|---|
| `a.mp4` | 120 s, H.264 High 1080p30 (~1.6 Mb/s as encoded), AAC 2.0 at 440 Hz, MP4 with the index at the front, 24,631,025 bytes |
| `b.mkv` | 120 s, HEVC Main 10 1080p30, E-AC-3 5.1 at 660 Hz, Matroska, 12,928,237 bytes |
| `m.mkv` | `a.mp4`'s video; audio 1 English AAC 2.0 440 Hz 128k (default), 2 Japanese AC-3 5.1 880 Hz 384k, 3 English "Commentary" AAC 2.0 220 Hz 96k (comment); subtitles 4 English SRT, 5 English ASS (yellow italic, top), 6 English PGS (forced). Each subtitle line names its own track and time (`SRT ENGLISH 0:08`), so a screenshot shows which track is on screen and whether it is in sync. FFmpeg writes no PGS, so `mksubs.py` writes the `.sup` itself (PIL text, RLE-encoded, decoded back as its self-check) |

Each play: open the details page, press Watch (or Resume), wait 15 s, screenshot, seek to about 60%
(71.4 s; the seek bar spans the file's real 120 s), wait 9 s, screenshot. Evidence, per play: rex's log
(`DECISION`, `PART`, `TRANSCODE` and FFmpeg's command line, `TIMELINE`), the Plex app's own log
(`/data/data/com.plexapp.android/files/logs/log*.txt`: `[MDE]` decisions, ExoPlayer's decoders and
`audioInputFormat`), Plezy's performance overlay (codec, channels and bitrate of the audio mpv decodes;
Playback Settings, shown only while the controls are up), and screenshots. Nobody listened to the phone:
audio is what the decoders logged, not what was heard.

## Part 1: one probe field at a time (proxy mode)

What rex showed (`/library/sections/1/all`) and what each app did. `a.mp4` copies are A, `b.mkv` copies B.

| | The probe said | rex's Media | Plex for Android | Plezy |
|---|---|---|---|---|
| A00 | the truth | mp4 h264 1080 aac 2ch, 1642 kb/s | direct play, first frame ~1 s, seek fine | plays, seek fine |
| A01 | HEVC Main 10 | hevc | direct play (the Pixel decodes HEVC), label "1080p (HEVC Main 10)" | plays |
| A02 | ProRes | prores | **conversion**: `video=prores->h264 1920x1080 2257k audio=copy`, EncodeOnly VAAPI; first segment 7 s after the decision, the picture at 8 s instead of ~1; the seek restarts FFmpeg at segment 35 | plays; label only |
| A03 | 3840x2160 | 4k | direct play, label "4K" | plays |
| A04 | 320x240 | sd | direct play, label "SD" | plays |
| A05 | 10 s long | duration 10000 | plays all 120 s (ExoPlayer reads the real length); **every timeline past 10 s answered 400**, no resume point (`viewOffset` none) | plays; its timelines carry mpv's 120 s, progress saved |
| A06 | 7200 s long | duration 7200000 | plays, progress saved as 89.6 s of 7,200 | plays, progress saved |
| A07 | TrueHD 7.1 | truehd 8ch | **conversion**: `video=copy audio=truehd->aac 6ch` (FFmpeg reads the real stereo AAC); app log "trueHD dropped, the output cannot carry it" | plays the real AAC; the details page says TrueHD 7.1 |
| A08 | Matroska (it is MP4) | mkv | direct play: the app's decision ignores the container | plays |
| A09 | 150 Mb/s | bitrate 150000 | direct play at Original | plays |
| A10 | HEVC Main 10, HDR10, Dolby Vision 8.6 | hevc | direct play, label "1080p DoVi/HDR10 (HEVC Main 10)" | plays |
| A11 | an extra Japanese AC-3 5.1 audio and a forced PGS that do not exist | **audio ac3 6ch** (the ghost, which also carried `default`) | direct play of the real AAC; the ghost tracks are listed, "English Forced (PGS)" among the subtitles | plays the real track |
| A12 | no audio stream | no audio | direct play; no Audio row on the details page (the player decodes the file's AAC, not heard) | plays |
| A13 | no video stream | no video | **conversion with `video=none audio=copy`: a black screen with sound** | plays the picture |
| A14 | no `format` block | no duration, no bitrate | direct play; no runtime, no resume point | plays; no resume point either |
| A15 | `format.size` ten times the real one | size 246,310,250 | rex answers `200 len=246310250` and sends 24.6 MB; the app asks `bytes=24631025-` (the real end) three times and gets `206` promising 221,679,225 bytes and sending none, then **asks for a conversion**: `video=h264->h264 1655k audio=aac->aac`, Full VAAPI, a re-encode of a playable file | **stuck on "Loading video"**: the same `bytes=24631025-` read seven times |
| A16 | `format.size` a tenth of the real one | size 2,463,102 | rex answers `200 len=2463102`, cutting the body there; ~16 s play, the seek gets **416**, then a conversion, which plays to the end | **stops at ~11.5 s and reports `stopped t=120000`: marked watched** (viewCount 2 after two runs) |
| B00 | the truth (HEVC 10-bit, E-AC-3 5.1) | mkv hevc eac3 6ch | **conversion**: `video=copy audio=eac3->aac 6ch`. App log: "eac3 is supported by platform", then "eac3 is not supported on this device (Google Pixel 6)" | plays E-AC-3 5.1 |
| B01 | H.264, AAC 2.0, MP4 | h264 aac mp4 | direct play: ExoPlayer found HEVC and E-AC-3 in the file and decoded them (`c2.exynos.hevc.decoder`, `c2.dolby.eac3.decoder.eac3`) | plays |
| B02 | AAC 2.0 (it is E-AC-3 5.1) | hevc aac 2ch | **direct play**: the app's decision said "aac is supported", ExoPlayer decoded the real E-AC-3 with the Dolby decoder. Better than the truth (B00) | plays |
| A99 | no `media_info` | held | not shown (`held: 1`), as documented | not shown |

### What decides what

1. **The Plex app decides on the phone, before rex is asked.** Its log reads `[MDE][Video] h264 is
   supported by platform`, `[MDE] Application decision, directPlay: true`, then the decision request goes
   to rex with `directPlay=1`. The codecs it weighs are the probe's (rex's Media); the container is not
   weighed (A08). So a codec name decides direct play outright: a false "unsupported" one (ProRes, TrueHD)
   converts a playable file, and a false "supported" one would direct-play a file the device cannot decode
   (B01 and B02 worked only because the Pixel decodes HEVC 10-bit and E-AC-3; not tested on a device
   that does not).
2. **The app's E-AC-3 rule is wrong for the Pixel 6.** It refuses E-AC-3 by device model although
   `c2.dolby.eac3.decoder.eac3` is on the phone and plays it (B02). Every honest E-AC-3 file converts its
   audio on rex.
3. **Plezy asks for no decision.** It reads the Part (`range=bytes=0-`, `206`, and a `404` for
   `/library/parts/<id>/indexes/sd`), mpv probes the file itself, and its Audio & Subtitles sheet lists the
   file's tracks. Its details page and the duration it reports come from rex.
4. **rex trusts the probe's size over its source** (proxy mode): `Content-Length`, `Content-Range` and the
   `416` bound are computed from `format.size` (or the item's `size`), whatever the source sends. A15 and
   A16 are that, seen from each app.
5. **rex refuses a timeline beyond the item's duration** (A05: `400 GET /:/timeline` six times, 9.1 s
   accepted, 16.2 s and 71.4 s refused), so the app's progress is lost for a too-short duration.
6. **A conversion maps what the probe lists**: no video stream listed, no video in the conversion (A13).

## Part 2: the same lies under `--part-mode redirect`

rex restarted with `--part-mode redirect`, the source answering every read with a 302 to the range server.
Direct play: the phone downloads from that server itself (one phone read per play in its log), rex sends
two `HEAD`s; a conversion still reads through rex (`TRANSCODE`, rex's address in the server's log).

| | Proxy mode | Redirect mode |
|---|---|---|
| A00-A04, A06, A08-A12, A14, B01, B02 | as above | the same, the phone reading the source |
| A15 (size ×10) | Plezy hangs; Plex re-encodes | **both play to the end**, Plex in direct play (no conversion), progress saved |
| A16 (size ÷10) | Plezy stops at ~11.5 s marked watched; Plex converts after a seek | **both play normally**; progress saved, not marked watched |
| A02, A07, B00 | conversions on rex | the same conversions, **read through rex** (redirect's saving lost) |
| A13 | black, sound only | the same |
| A05 | timelines past 10 s refused | the same |
| A14 | no resume point | the same |

The size stops mattering because the client gets the file's real length from the source. Everything the
app decides from the probe, and everything rex decides from the duration, is the same.

## Part 3: tracks (`m.mkv`, proxy mode, then redirect)

### The variants and rex's own default pick

rex marks one audio and one subtitle stream `selected` before anything plays (the owner's account:
English audio and subtitles). Stream ids are rex's (`<ratingKey>00N`); `index` is the probe's.

| | The probe | rex's selected audio | rex's selected subtitle |
|---|---|---|---|
| M00 | the truth | index 1, English AAC | SRT |
| M01 | streams 2 and 3 described the wrong way round (index 2 says English Commentary AAC, index 3 Japanese AC-3 5.1) | index 1 | SRT |
| M02 | languages swapped (index 1 says Japanese, index 2 English) | **index 2, the real Japanese AC-3** (labelled English) | SRT |
| M03 | the SRT not listed | index 1 | **ASS** |
| M04 | a French SRT listed at index 7 that does not exist | index 1 | SRT |
| M05 | the forced flag on the SRT instead of the PGS | index 1 | **ASS** |
| M06 | the Japanese audio not listed (indexes kept: 1, 3, 4, 5, 6) | index 1 | SRT |
| M07 | the default flag on the Japanese instead of the English | index 1 (the account's language wins) | SRT |

### Plex for Android

Picked on the details page (each pick is a `PUT /library/parts/<id>?audioStreamID=` or
`subtitleStreamID=` to rex, kept per account and shared by every app), then played. What was decoded is
the app's own log: `[TrackSelector] Override added 'audio/ac3 null japanese'` and ExoPlayer's
`audioInputFormat id=2 ... channels=6, language=japanese`. ExoPlayer's track ids are the file's stream
indexes.

| | Pick (as the app labels it) | Decoded |
|---|---|---|
| M00 | nothing | track 3 (Commentary) for an instant, then track 1 (English), SRT on screen |
| M00 | 2nd, "Japanese (AC3 5.1)" / 3rd, "English (AAC Stereo)" | track 2 / track 3, as labelled |
| M00 | SRT / ASS / PGS | each on screen, ASS with its styling, still on screen after the seek (its time against the picture's was not compared) |
| M00 | subtitles Off | **the forced PGS is not shown** |
| M01 | 2nd, "English (AAC Stereo)" | **track 2, the Japanese AC-3** |
| M01 | 3rd, "Japanese (AC3 5.1)" | **track 3, the Commentary** |
| M02 | nothing | **track 2, Japanese**, for an English-language account |
| M02 | 1st, "Japanese (AAC Stereo)" | track 1, English |
| M03 | (SRT) | cannot be picked: Off, ASS, PGS listed |
| M04 | "French (SRT)" | nothing on screen, no error |
| M05 | nothing | the ASS on screen; picked SRT and PGS both shown |
| M06 | (Japanese) | cannot be picked: two audio entries |
| M07 | nothing | English |

Every one of these was direct play: the Pixel decodes AC-3 too, and the player renders SRT, ASS and PGS.
The app asks the player for the stream at the probe's index, then names it by the probe's labels.

### rex's conversions

To make rex choose the streams itself, the player's quality was set to 320p (720 kb/s), below the file's
bitrate; at 720p (4 Mb/s) the app still direct-played this 2 Mb/s file. The in-player quality went back to
Original on the next play. FFmpeg's command line in rex's log:

| | Pick | FFmpeg maps | Note |
|---|---|---|---|
| M00 | Japanese | `-map 0:0 -map 0:2` | `audio=ac3->aac 2ch` |
| M00 | Commentary | `-map 0:3` | |
| M00 | English + forced PGS | `[0:6]scale=...,format=yuva420p,hwupload[sub];[main][sub]overlay_vaapi` and `-map 0:1` | burned in and legible |
| M01 | 2nd, "English (AAC)" | **`-map 0:2`, the Japanese AC-3**; rex logs `audio=aac->aac` | rex and the app agree on the index |
| M01 | 3rd, "Japanese (AC3)" | `-map 0:3`, the Commentary | |
| M02 | 2nd, "English (AC3)" | `-map 0:2` | |
| M06 | 2nd, "English (AAC)" (the probe's index 3) | `-map 0:3` | a dropped stream with its index kept is no problem |

### Plezy

Its sheet listed the file's real tracks for every variant (M01, M02 and M06 included: Japanese AC-3 5.1
present and named right), and its overlay showed what each pick decoded: `AAC stereo 128 kbps`,
`AC3 5.1(side) 384 kbps`, `AAC stereo 96 kbps`. SRT, ASS (styled) and PGS rendered; with nothing picked it showed the forced PGS on its own, which the
Plex app did not. A pick in either app is a `PUT` to rex, kept per account, so the next play starts on it.

### Redirect mode

M01 again under `--part-mode redirect`: the Plex app's picks decoded the same tracks (2nd label → the
Japanese AC-3), conversions mapped the same indexes, PGS burned in, Plezy's subtitles rendered; the phone
read the file from the source in direct play (over the M01 runs in both apps and the conversions: 52 reads
from the phone, 59 from rex, which are its HEADs and the conversions' reads). Plezy's overlay was off for that run, so its audio there is not confirmed by it.

## What each field is for

| Field | Missing | Wrong |
|---|---|---|
| `streams[]` | held, never shown | |
| a video stream (`index`, `codec_type`, `codec_name`) | Plex app: black screen, sound only (a conversion with no video) | a codec the app refuses converts a playable file (ProRes); one it accepts direct-plays what the device may not decode (not tested) |
| each audio stream (`index`, `codec_type`, `codec_name`, `channels`, `tags.language`) | cannot be picked in the Plex app; a conversion would carry no audio (not tested) | the codec decides conversion (TrueHD); the language moves rex's default pick (M02 played Japanese); a wrong description plays another track than its label |
| `index` on every stream | | **the track played, in the app and in rex's conversions** |
| subtitle streams (`index`, `codec_name`, `tags.language`) | cannot be picked in the Plex app | a listed one that is not there shows nothing |
| `disposition.default` / `forced` | | the forced flag moves rex's default subtitle (M05) |
| `format.duration` | no runtime, no resume point (both apps) | too short: rex refuses timelines past it; too long: never watched |
| `format.size` (or the item's `size`) | not tested: rex then passes the client's `Range` to the source as it is (`docs/feed-api.md`) | proxy mode: Plezy hangs or stops and marks watched, the Plex app re-encodes; redirect mode: harmless for direct play |
| width, height, `profile`, `pix_fmt`, colour, Dolby Vision side data | | labels and library filters; would decide conversions under a quality cap or on a client without HDR (not tested) |
| `format.bit_rate` | | quality decisions away from home (not tested) |
| `format.format_name` | | nothing on Android; Plex Web cannot direct-play MKV (not tested here) |
| stream `title`, `codec_long_name`, timestamps, the rest | | nothing seen: the Plex app shows no track title |

What a source should do: send ffprobe's whole `-show_format -show_streams` output (`-show_chapters` too)
when it has the file. A source that cannot probe should send only what it knows and leave out what it does
not; it should never renumber streams after dropping one.

## What rex does that this showed

Observed, not changed by this study:

- **A part's size is the probe's** (proxy mode): a source that serves another length makes rex promise
  bytes it does not have, and answer `206` with none past the real end (A15), or cut the body short (A16).
  Using the length the source answers would make both play.
- **A timeline past the item's duration is refused with 400** (A05), losing the resume point.
- **The item's audio summary is the stream marked default**, even one the file does not have (A11's ghost
  AC-3 became the Media's `audioCodec`).
- **A conversion of an item whose probe lists no video maps no video** (A13).
- **`DELETE /myplex/account` answers 200 and unclaims nothing**: rex routes `/myplex/account` for every
  method to the account read. Unclaiming a lab rex is deleting its plex.tv device and its
  `claim_state.json`.
- **Plex Web 4.147.1 does not refresh a library grid when the feed adds rows**: an empty library stayed
  "is empty" after an item was pushed, until the page was reloaded.

## What came of it

- rex-stremio checks titles in parallel (`probe_workers`, 4), sends each to rex as it is ready, takes DMM's
  stored probe for a torrent's file (hash, exact served size, and name when the stream names it) and sends
  the probes it takes back to DMM (`share_probes`) (rex-stremio `4aeef1e`, `26030bd`); DMM keeps those in
  `ContributedProbe` and answers them after zurg's snapshots (DMM `267b4703`). DMM held probes for 83 of
  Torrentio's 246 releases of The Matrix, and for 9 of the 10 it ranks first (2026-11-06).
- Measured on one 33-film catalog, the same addon, cold start: the old rex-stremio (`2b5446a`) showed
  nothing for 3:07.7 and then all 33; the new one showed the first at 0:06.4 and all 33 by 0:59.7.
- archive.org answered 1 of 99 ffprobe reads with a 5xx under four readers at once, which made rex-stremio
  set the stream aside for a day; it now asks once more first (rex-stremio `8fe679a`).

## Not tested

- Plex Web, Plex for iOS, Apple TV, TVs, Plex HTPC: the fields Android ignores (container, bitrate, HDR)
  are likely to matter there.
- A device that cannot decode HEVC 10-bit, E-AC-3 or AC-3, where a false "supported" codec would fail.
- Quality caps (remote, cellular) with false resolution or bitrate.
- Leaving out `format.size` and the item's `size` altogether.
- Probes whose stream indexes are renumbered after a dropped stream.
- Listening: audio is what the decoders logged.
- Playing to the end: "never watched" for a too-long duration is inferred from the timelines.

## Reproducing it

`probes/probe-study/`:

| File | What |
|---|---|
| `make_media.sh` | the three test files (`mksubs.py` writes the SRT, ASS and PGS) |
| `variants.py` | Part 1's variants of `a.mp4` and `b.mkv`, pushed to a rex as the source `lab` |
| `variants2.py` | Part 3's variants of `m.mkv` |
| `cdn.py` | a byte server with `Range` that logs every read, behind a loopback address that 302s to it |
| `streams.py` | rex's audio and subtitle streams per item, with `selected`, `default` and `forced` |
| `ui.py` | a tiny adb `uiautomator` helper: dump, find, tap |
| `plexrun.sh`, `plezyrun.sh`, `summ.py` | Part 1's plays in each app and their summary |
| `plextracks.py`, `tracklog.py`, `plexconvert.py`, `plezytracks.py` | Part 3's plays: the Plex app's track matrix and what its log decoded, its forced conversions, and Plezy's track sheet with its overlay |

The drivers expect the lab rex's log in the user unit `rexlab` on `$REXLAB_SSH` (default `zen`), the phone
on `$ADB_SERIAL`, Plex for Android and Plezy signed in to the account the lab rex is claimed to, and, for
Plezy's audio, its performance overlay switched on (the drivers toggle it each run; check it before
relying on a screenshot).
