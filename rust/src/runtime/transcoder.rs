//! `/photo/:/transcode` -- the photo transcoder, ported from `server.py`'s `_route`.
//!
//! Cycles 27-52, measured on plexlab (1.43.4) and the 1.42.2 reference: the dimensions are
//! validated first, then five more parameters, then the `url=`. A relative url is FETCHED
//! through the server's own route table and the answer follows that fetch -- anything but a
//! 200 is a 404, a 200 that is not an image is the 500, and an image is the direct path's
//! own bytes -- so the transcoder never serves a picture the direct path refuses.

use super::*;
use regex::Regex;
use std::sync::OnceLock;

/// `_photo_int`: `0`, `1`, `01` and `+1` parse; `1.5`, empty, `abc` and a space do not.
fn photo_int(value: &str) -> bool {
    let digits = value.trim_start_matches(['+', '-']);
    !value.is_empty()
        && !digits.is_empty()
        && digits.bytes().all(|b| b.is_ascii_digit())
        && value
            .chars()
            .next()
            .is_some_and(|c| c == '+' || c == '-' || c.is_ascii_digit())
}

/// `_bool_option`'s grammar for a 0/1 value: `+?0*[01]`.
fn photo_bool(value: &str) -> bool {
    let digits = value.strip_prefix('+').unwrap_or(value);
    let trimmed = digits.trim_start_matches('0');
    !digits.is_empty()
        && digits.bytes().all(|b| b.is_ascii_digit())
        && (trimmed.is_empty() || trimmed == "1")
}

/// `urllib.parse.urlparse`'s scheme: letters first, then letters, digits, `+-.`, then `:`.
fn url_scheme(target: &str) -> Option<String> {
    let (scheme, _) = target.split_once(':')?;
    let mut chars = scheme.chars();
    let first = chars.next()?;
    if !first.is_ascii_alphabetic() {
        return None;
    }
    if !chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.')) {
        return None;
    }
    Some(scheme.to_ascii_lowercase())
}

thread_local! {
    /// How many transcodes deep the current inner fetch is. Plexamp names the transcoder
    /// inside its own `url` (`/photo/:/transcode?url=%2Fphoto%2F%3A%2Ftranscode%3F...`) for an
    /// artist's and album's header art, and plexav (1.43.4, 2026-10-31) answered that 200 at
    /// two and three levels; the oracle's one-level refusal does not hold for it.
    static TRANSCODE_DEPTH: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
    /// The transcoder's OWN `width`/`height` while its inner fetch runs: an item with
    /// no image to resolve is the oracle's `("png", query)`, the generated tile at the
    /// size the TRANSCODER was asked for, not the direct path's 342x513.
    pub(super) static TRANSCODE_SIZE: std::cell::Cell<Option<(u32, u32)>> =
        const { std::cell::Cell::new(None) };
}

impl Runtime {
    pub(super) fn route_photo_transcode(
        &mut self,
        request: &Request,
        query: &HashMap<String, String>,
    ) -> Response {
        for name in ["width", "height"] {
            let Some(value) = query.get(name).filter(|value| !value.is_empty()) else {
                return Response::photo_error(400, "rex: bad request");
            };
            if !photo_int(value) {
                return Response::photo_error(400, "rex: bad request");
            }
        }
        for name in ["opacity", "quality", "blur", "minSize", "upscale"] {
            let Some(value) = query.get(name) else {
                continue;
            };
            if !photo_int(value) {
                return Response::photo_error(400, "rex: bad request");
            }
            if matches!(name, "minSize" | "upscale") && !photo_bool(value) {
                return Response::photo_error(400, "rex: bad request");
            }
            if name == "blur"
                && value.starts_with('-')
                && value.trim_start_matches(['-', '0']) != ""
            {
                return Response::photo_error(500, "rex: server error");
            }
        }
        let Some(url) = query.get("url") else {
            return Response::photo_error(400, "rex: bad request");
        };
        let target = percent_decode(url);
        let scheme = url_scheme(&target);
        let transcoded = |mut response: Response| {
            response.headers.retain(|(name, _)| {
                !matches!(
                    name.as_str(),
                    "Cache-Control" | "Accept-Ranges" | "X-Plex-Local-Asset"
                )
            });
            response
                .headers
                .push(("Accept-Ranges".to_string(), "bytes".to_string()));
            response
                .headers
                .push(("Cache-Control".to_string(), "max-age=259200".to_string()));
            response
        };
        if matches!(scheme.as_deref(), Some("http" | "https")) {
            // An absolute url (a person thumb, or art handed out earlier) is fetched as
            // it is, at the TMDB variant nearest the width, once the lock is released.
            self.pending_image = Some(tmdb_at_width(
                &target,
                query.get("width").map(String::as_str),
            ));
            let mut response = Response::bytes(200, "image/jpeg");
            response.headers = vec![
                ("X-Plex-Protocol".to_string(), "1.0".to_string()),
                ("Date".to_string(), http_date()),
            ];
            return transcoded(response);
        }
        if scheme.is_some() || !target.starts_with('/') {
            return Response::photo_error(404, "rex: not found");
        }
        let (inner, _) = target.split_once('?').unwrap_or((target.as_str(), ""));
        if let Some(relative) = inner.strip_prefix("/:/resources/") {
            let response = self.route_bundled_resource(relative, false);
            if response.status != 200 {
                return Response::photo_error(response.status, "rex: not found");
            }
            let mut response = response;
            response
                .headers
                .insert(0, ("X-Plex-Protocol".to_string(), "1.0".to_string()));
            response.headers.push(("Date".to_string(), http_date()));
            return transcoded(response);
        }
        // Three levels measured; past eight a crafted url is refused rather than followed.
        if TRANSCODE_DEPTH.with(|cell| cell.get()) >= 8 {
            return Response::photo_error(404, "rex: not found");
        }
        let segments = inner
            .split('/')
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>();
        if segments.last() == Some(&"metadata") && segments.contains(&"theme") {
            // A GET that MINTS a row must not run from here.
            return Response::photo_error(500, "rex: server error");
        }
        let mut headers = super::inner_headers(request);
        headers.insert("accept".to_string(), "application/json".to_string());
        // The transcoder's own width picks the composite's upstream variant, as the
        // oracle's `_at_width` does on the fetched image.
        let mut inner_target = target.clone();
        if inner.starts_with("/library/sections/")
            && inner.contains("/composite/")
            && !target.contains('?')
        {
            if let Some(width) = query.get("width") {
                inner_target = format!("{inner}?width={width}");
            }
        }
        let inner_request = Request {
            method: "GET".to_string(),
            target: inner_target,
            version: request.version.clone(),
            headers,
            body: Vec::new(),
            body_complete: true,
        };
        let dimension = |name: &str| {
            query
                .get(name)
                .and_then(|value| value.trim().parse::<i64>().ok())
                .map(|value| value.clamp(1, 2000) as u32)
                .unwrap_or(1)
        };
        let outer_size = TRANSCODE_SIZE.with(|cell| cell.replace(Some((dimension("width"), dimension("height")))));
        TRANSCODE_DEPTH.with(|cell| cell.set(cell.get() + 1));
        let fetched = self.request_route(&inner_request);
        TRANSCODE_DEPTH.with(|cell| cell.set(cell.get() - 1));
        TRANSCODE_SIZE.with(|cell| cell.set(outer_size));
        if fetched.status != 200 {
            // Every refusal the direct path makes -- 400, 404, 500 -- is a 404 here.
            return Response::photo_error(404, "rex: not found");
        }
        // Cycle 83: an upload is served `image/jpeg` whatever its bytes, and the
        // transcoder DECODES them -- 32 bytes of text is its 500 on plexscratch42 -- so a
        // jpeg-labelled body that is no image is refused here too. (A remote image's
        // body is still empty at this point: it is fetched after the lock.)
        if !fetched.content_type.starts_with("image/")
            || (fetched.content_type == "image/jpeg"
                && !fetched.body.is_empty()
                && !artwork::image_type(&fetched.body).starts_with("image/"))
        {
            self.pending_image = None;
            return Response::photo_error(500, "rex: server error");
        }
        // The direct path answered with a remote image it has not fetched yet: the
        // transcoder asks for it at the variant nearest ITS width (`_at_width` over the
        // inner result), and the fetch happens once the lock is released.
        if let Some(url) = self.pending_image.take() {
            self.pending_image = Some(tmdb_at_width(
                &url,
                query.get("width").map(String::as_str),
            ));
        }
        transcoded(Response {
            status: 200,
            content_type: fetched.content_type,
            body: fetched.body,
            headers: vec![
                ("X-Plex-Protocol".to_string(), "1.0".to_string()),
                ("Date".to_string(), http_date()),
            ],
            close: false,
            doc: None,
        })
    }
}

/// `_at_width` (`TMDB_WIDTHS`): rex cannot resize, so a TMDB url is re-pointed at the
/// smallest published variant at least as wide as the client asked for.
pub(super) fn tmdb_at_width(url: &str, width: Option<&str>) -> String {
    const TMDB_WIDTHS: [i64; 7] = [92, 154, 185, 342, 500, 780, 1280];
    let Some(wanted) = width.and_then(|value| value.trim().parse::<i64>().ok()) else {
        return url.to_string();
    };
    if !url.contains("image.tmdb.org") {
        return url.to_string();
    }
    let target = TMDB_WIDTHS
        .iter()
        .find(|variant| **variant >= wanted)
        .map(|variant| format!("w{variant}"))
        .unwrap_or_else(|| "original".to_string());
    let Ok(pattern) = Regex::new(r"/t/p/(w\d+|original)/") else {
        return url.to_string();
    };
    pattern
        .replace(url, format!("/t/p/{target}/").as_str())
        .into_owned()
}

/// `_serve_image`'s upstream half: fetch a remote artwork url once, cached in memory
/// (bounded, as the oracle's is), over plain HTTP or TLS against the system roots. A source's
/// own artwork comes with its headers (`from_source`) and may be on rex's own networks, as the
/// source is; every other url is public only (`fetch_public`).
pub(super) fn fetch_remote_image(url: &str, from_source: Option<&[(&'static str, String)]>) -> Option<(Vec<u8>, String)> {
    static CACHE: OnceLock<Mutex<HashMap<String, (Vec<u8>, String)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    // Kept apart: a client naming a source's artwork url (`/photo/:/transcode?url=`) must not
    // be served what the source's own fetch cached, which `fetch_public` would have refused.
    let key = if from_source.is_some() { format!("source {url}") } else { url.to_string() };
    let cached = cache.lock().ok().and_then(|map| map.get(&key).cloned());
    info!(
        "ARTWORK {} {}",
        if cached.is_some() { "cache" } else { "fetch" },
        url.chars().take(110).collect::<String>()
    );
    if let Some(hit) = cached {
        return Some(hit);
    }
    let short = url.chars().take(80).collect::<String>();
    let fetched = match from_source {
        Some(headers) => super::upstream::fetch("GET", url, headers, b"", std::time::Duration::from_secs(20)),
        None => super::upstream::fetch_public(
            "GET",
            url,
            // `_serve_image`'s urllib request: the upstream User-Agent and no `Accept`.
            &[("User-Agent", super::upstream::UPSTREAM_UA.to_string())],
            std::time::Duration::from_secs(20),
        ),
    };
    let (status, headers, body) = match fetched {
        Ok(fetched) => fetched,
        Err(error) => {
            debug!("artwork fetch failed for {short}: <urlopen error {error}>");
            return None;
        }
    };
    if !(200..300).contains(&status) {
        debug!(
            "artwork fetch failed for {short}: HTTP Error {status}: {}",
            super::wire::reason_phrase(status)
        );
        return None;
    }
    let content_type = headers
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case("content-type"))
        .map(|(_, value)| value.clone())
        .unwrap_or_else(|| "image/jpeg".to_string());
    let hit = (body, content_type);
    if let Ok(mut map) = cache.lock() {
        remember_image(&mut map, &key, hit.clone());
    }
    Some(hit)
}

/// Plex's artwork is full size (a background runs to megabytes), so 512 entries alone could
/// hold a gigabyte on a host with 8 GB; the cache is bounded by bytes too.
const IMAGE_CACHE_BYTES: usize = 64 << 20;

fn remember_image(map: &mut HashMap<String, (Vec<u8>, String)>, url: &str, hit: (Vec<u8>, String)) {
    remember_within(map, url, hit, 512, IMAGE_CACHE_BYTES);
}

fn remember_within(map: &mut HashMap<String, (Vec<u8>, String)>, key: &str, hit: (Vec<u8>, String), entries: usize, bytes: usize) {
    let held: usize = map.values().map(|(body, _)| body.len()).sum();
    if map.len() > entries || held + hit.0.len() > bytes {
        map.clear();
    }
    if hit.0.len() <= bytes {
        map.insert(key.to_string(), hit);
    }
}

/// A fitted poster is tens of kilobytes, so the same budget holds thousands of them.
const FITTED_CACHE_ENTRIES: usize = 8192;

static FITTED: OnceLock<Mutex<HashMap<String, (Vec<u8>, String)>>> = OnceLock::new();

/// The disk cache's file name carries the answer's type, so a kept picture reads back as it was served.
const DISK_TYPES: [(&str, &str); 4] = [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp"), ("image/gif", "gif")];

/// `/photo/:/transcode`'s answer scaled to the `width`x`height` the client asked for, as a real
/// PMS scales it: `minSize=1` covers the box, otherwise the picture fits inside it, and nothing
/// grows unless `upscale=1`; `format=png` or `format=jpeg` names the answer's type, as a real
/// PMS honours it (PNG otherwise stays PNG for its alpha, anything else is JPEG). Plex's agent
/// artwork is the original (a poster 1920x2880, ~800 KB) where a grid asks for 240x360, so
/// unscaled a page of posters is tens of megabytes. The answer is kept by the picture's url and
/// the box, in memory and, under `REX_ARTWORK_CACHE_MB`, on disk, so a hit fetches nothing.
pub(super) struct PhotoFit {
    key: String,
    width: i64,
    height: i64,
    cover: bool,
    upscale: bool,
    format: Option<&'static str>,
    ffmpeg: std::path::PathBuf,
    dir: Option<std::path::PathBuf>,
    /// Only a picture that cannot change under its url is kept: versioned artwork, a bundled
    /// resource, or an outside url. `/library/metadata/7/thumb` follows a poster pick, so it is fitted but never kept.
    keep: bool,
}

impl PhotoFit {
    /// `None` without both dimensions or a `url`; `dir` is where the disk cache would live.
    pub(super) fn new(query: &HashMap<String, String>, ffmpeg: std::path::PathBuf, dir: std::path::PathBuf) -> Option<Self> {
        let dimension = |name: &str| query.get(name).and_then(|value| value.trim().parse::<i64>().ok()).map(|value| value.clamp(1, 2000));
        let (width, height) = (dimension("width")?, dimension("height")?);
        let flag = |name: &str| query.get(name).is_some_and(|value| photo_bool(value) && value.trim_start_matches(['+', '0']) == "1");
        let (cover, upscale) = (flag("minSize"), flag("upscale"));
        let format = match query.get("format").map(|value| value.to_ascii_lowercase()).as_deref() {
            Some("png") => Some("image/png"),
            Some("jpeg" | "jpg") => Some("image/jpeg"),
            _ => None,
        };
        let raw = percent_decode(query.get("url")?);
        let outside = matches!(url_scheme(&raw).as_deref(), Some("http" | "https"));
        let path = raw.split('?').next().unwrap_or_default();
        let segments = path.split('/').filter(|segment| !segment.is_empty()).collect::<Vec<_>>();
        let keep = outside
            || path.starts_with("/:/resources/")
            || matches!(segments.as_slice(), ["library", "metadata", _, _, stamp] if stamp.bytes().all(|b| b.is_ascii_digit()));
        // rex's own token differs per viewer and grants nothing here (the route already answered);
        // an outside url's credentials stay part of its identity.
        let url = if outside { raw.clone() } else { logging::redact_tokens(&raw) };
        let digest = Sha1::digest(format!("{url} {width}x{height} {cover} {upscale} {format:?}").as_bytes());
        let key = digest.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
        let enabled = artwork_cache_budget().is_some();
        Some(Self { key, width, height, cover, upscale, format, ffmpeg, dir: enabled.then(|| dir.join("artwork-cache")), keep })
    }

    /// A kept answer, from memory or disk.
    pub(super) fn cached(&self) -> Option<(Vec<u8>, String)> {
        if !self.keep {
            return None;
        }
        let memory = FITTED.get_or_init(Default::default);
        if let Some(hit) = memory.lock().ok().and_then(|map| map.get(&self.key).cloned()) {
            return Some(hit);
        }
        let dir = self.dir.as_ref()?;
        let (path, content_type) = DISK_TYPES
            .into_iter()
            .map(|(content_type, ext)| (dir.join(format!("{}.{ext}", self.key)), content_type))
            .find(|(path, _)| path.is_file())?;
        let body = std::fs::read(&path).ok()?;
        // Read is use: the oldest-read file is the first pruned.
        let _ = std::fs::File::options().write(true).open(&path).and_then(|file| file.set_modified(std::time::SystemTime::now()));
        let hit = (body, content_type.to_string());
        if let Ok(mut map) = memory.lock() {
            remember_within(&mut map, &self.key, hit.clone(), FITTED_CACHE_ENTRIES, IMAGE_CACHE_BYTES);
        }
        Some(hit)
    }

    /// Scale the answer the route fetched; any failure leaves it as it was.
    pub(super) fn apply(&self, response: &mut Response) {
        if response.status != 200 || response.body.is_empty() || !response.content_type.starts_with("image/") {
            return;
        }
        let target = self.format.unwrap_or(if response.content_type == "image/png" { "image/png" } else { "image/jpeg" });
        let (width, height) = (self.width, self.height);
        let (w, h) = if self.upscale { (width.to_string(), height.to_string()) } else { (format!("min({width}\\,iw)"), format!("min({height}\\,ih)")) };
        let filter = format!("scale=w={w}:h={h}:force_original_aspect_ratio={}", if self.cover { "increase" } else { "decrease" });
        let Some(body) = scale_image(&self.ffmpeg, &response.body, &filter, target == "image/png") else {
            static WARNED: std::sync::Once = std::sync::Once::new();
            WARNED.call_once(|| warning!("photo transcode: {} could not scale a picture; originals are served", self.ffmpeg.display()));
            // In memory only, so the picture is not tried again each request, nor kept unscaled past a restart.
            if let Some(mut map) = self.keep.then(|| FITTED.get_or_init(Default::default).lock().ok()).flatten() {
                remember_within(&mut map, &self.key, (response.body.clone(), response.content_type.clone()), FITTED_CACHE_ENTRIES, IMAGE_CACHE_BYTES);
            }
            return;
        };
        // A picture already as small as the box goes out as it came, unless it was asked to grow or change type.
        let converted = target != response.content_type;
        if !body.is_empty() && (body.len() < response.body.len() || self.upscale || (converted && self.format.is_some())) {
            response.body = body;
            response.content_type = target.to_string();
        }
        self.remember((response.body.clone(), response.content_type.clone()));
    }

    fn remember(&self, hit: (Vec<u8>, String)) {
        if !self.keep {
            return;
        }
        // On disk only as a type `cached` reads back; any other stays in memory.
        let ext = DISK_TYPES.iter().find(|(content_type, _)| *content_type == hit.1).map(|(_, ext)| ext);
        if let (Some(dir), Some(ext)) = (&self.dir, ext) {
            static WRITES: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
            let write = WRITES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            // Two requests for one picture must not share a partial file.
            let partial = dir.join(format!("{}.{ext}.{}-{write}.partial", self.key, std::process::id()));
            if std::fs::create_dir_all(dir).is_ok() && std::fs::write(&partial, &hit.0).is_ok() {
                let _ = std::fs::rename(&partial, dir.join(format!("{}.{ext}", self.key)));
            }
            if let Some(budget) = artwork_cache_budget().filter(|_| write.is_multiple_of(128)) {
                prune_artwork_cache(dir, budget);
            }
        }
        if let Ok(mut map) = FITTED.get_or_init(Default::default).lock() {
            remember_within(&mut map, &self.key, hit, FITTED_CACHE_ENTRIES, IMAGE_CACHE_BYTES);
        }
    }
}

/// `REX_ARTWORK_CACHE_MB`: the disk cache's budget in megabytes; unset or 0 keeps it off.
fn artwork_cache_budget() -> Option<u64> {
    static BUDGET: OnceLock<Option<u64>> = OnceLock::new();
    *BUDGET.get_or_init(|| std::env::var("REX_ARTWORK_CACHE_MB").ok()?.trim().parse::<u64>().ok().filter(|mb| *mb > 0).map(|mb| mb << 20))
}

/// The least recently read files go until the cache is under 90% of `budget` bytes.
fn prune_artwork_cache(dir: &std::path::Path, budget: u64) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut files = entries
        .flatten()
        .filter_map(|entry| {
            let meta = entry.metadata().ok()?;
            Some((meta.modified().ok()?, meta.len(), entry.path()))
        })
        .collect::<Vec<_>>();
    let mut held: u64 = files.iter().map(|(_, len, _)| len).sum();
    files.sort();
    for (_, len, path) in files {
        if held <= budget / 10 * 9 {
            break;
        }
        if std::fs::remove_file(&path).is_ok() {
            held -= len;
        }
    }
}

/// One picture through ffmpeg on pipes: `filter` applied, written back as JPEG or PNG.
fn scale_image(ffmpeg: &std::path::Path, image: &[u8], filter: &str, png: bool) -> Option<Vec<u8>> {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let mut args = vec!["-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-frames:v", "1", "-vf", filter, "-f", "image2pipe"];
    args.extend(if png { ["-c:v", "png", "-pix_fmt", "rgba"] } else { ["-c:v", "mjpeg", "-pix_fmt", "yuvj420p"] });
    args.extend(["-q:v", "3", "pipe:1"]);
    let mut child = Command::new(ffmpeg).args(&args).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let mut stdin = child.stdin.take()?;
    let input = image.to_vec();
    // Written from its own thread: ffmpeg may start answering before it has read everything.
    let writer = std::thread::spawn(move || stdin.write_all(&input));
    let output = child.wait_with_output().ok()?;
    let _ = writer.join();
    output.status.success().then_some(output.stdout)
}

#[cfg(test)]
mod image_cache_tests {
    use super::*;

    #[test]
    fn the_image_cache_never_holds_more_than_its_byte_budget() {
        let mut map = HashMap::new();
        let image = |n: usize| (vec![0u8; n], "image/jpeg".to_string());
        for i in 0..40 {
            remember_image(&mut map, &format!("u{i}"), image(3 << 20));
            assert!(map.values().map(|(body, _)| body.len()).sum::<usize>() <= IMAGE_CACHE_BYTES);
        }
        remember_image(&mut map, "huge", image(IMAGE_CACHE_BYTES + 1));
        assert!(!map.contains_key("huge"));
    }

    #[test]
    fn the_artwork_cache_reads_back_from_disk_and_prunes_the_least_recently_read() {
        let dir = std::env::temp_dir().join(format!("rex-artwork-cache-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let query = [("width", "240"), ("height", "360"), ("url", "/library/metadata/7/thumb/7?X-Plex-Token=abc")]
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect::<HashMap<_, _>>();
        let mut fit = PhotoFit::new(&query, "ffmpeg".into(), dir.clone()).unwrap();
        fit.dir = Some(dir.clone());
        // Only what cannot change under its url is kept; rex's own token is not part of the key, an outside url's is.
        let fit_of = |url: &str| PhotoFit::new(&[("width".to_string(), "1".to_string()), ("height".to_string(), "1".to_string()), ("url".to_string(), url.to_string())].into_iter().collect(), "ffmpeg".into(), dir.clone()).unwrap();
        assert!(!fit_of("/library/metadata/7/thumb").keep);
        assert!(fit_of("/:/resources/movie.png").keep && fit_of("https://example.com/a.jpg").keep);
        assert_eq!(fit_of("/library/metadata/7/thumb/7?X-Plex-Token=a").key, fit_of("/library/metadata/7/thumb/7?X-Plex-Token=b").key);
        assert_ne!(fit_of("https://example.com/a.jpg?token=a").key, fit_of("https://example.com/a.jpg?token=b").key);
        let forget = |fit: &PhotoFit| FITTED.get_or_init(Default::default).lock().unwrap().remove(&fit.key);
        // A kept JPEG comes back from disk, after a restart (memory forgotten), with its type.
        fit.remember((b"jpeg bytes".to_vec(), "image/jpeg".to_string()));
        forget(&fit);
        assert_eq!(fit.cached(), Some((b"jpeg bytes".to_vec(), "image/jpeg".to_string())));
        // An original WebP comes back as WebP, never as image/jpeg; an unknown type stays in memory.
        std::fs::remove_dir_all(&dir).unwrap();
        fit.remember((b"webp bytes".to_vec(), "image/webp".to_string()));
        forget(&fit);
        assert_eq!(fit.cached(), Some((b"webp bytes".to_vec(), "image/webp".to_string())));
        std::fs::remove_dir_all(&dir).unwrap();
        fit.remember((b"bmp bytes".to_vec(), "image/bmp".to_string()));
        forget(&fit);
        assert_eq!(fit.cached(), None);
        // Pruning removes the least recently read file first, down to 90% of the budget.
        std::fs::create_dir_all(&dir).unwrap();
        let now = std::time::SystemTime::now();
        for (name, age) in [("old.jpg", 30), ("mid.jpg", 20), ("new.jpg", 10)] {
            std::fs::write(dir.join(name), vec![0u8; 100]).unwrap();
            let file = std::fs::File::options().write(true).open(dir.join(name)).unwrap();
            file.set_modified(now - std::time::Duration::from_secs(age)).unwrap();
        }
        prune_artwork_cache(&dir, 250);
        let mut left = std::fs::read_dir(&dir).unwrap().map(|entry| entry.unwrap().file_name().into_string().unwrap()).collect::<Vec<_>>();
        left.sort();
        assert_eq!(left, ["mid.jpg", "new.jpg"]);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// Run where FFmpeg is installed:
    /// cargo test fits_a_poster_to_the_box -- --ignored
    /// REX_THUMBNAIL_TEST_FFMPEG may name an absolute FFmpeg executable.
    #[test]
    #[ignore = "requires real FFmpeg"]
    fn fits_a_poster_to_the_box() {
        use std::process::Command;
        let ffmpeg = std::path::PathBuf::from(std::env::var_os("REX_THUMBNAIL_TEST_FFMPEG").unwrap_or_else(|| "ffmpeg".into()));
        let poster = Command::new(&ffmpeg)
            .args(["-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x2880", "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "2", "pipe:1"])
            .output()
            .unwrap()
            .stdout;
        let fit = |pairs: &[(&str, &str)]| {
            let mut query = pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect::<HashMap<_, _>>();
            query.insert("url".to_string(), "/library/metadata/1/thumb/1".to_string());
            let fit = PhotoFit::new(&query, ffmpeg.clone(), std::env::temp_dir()).unwrap();
            let mut response = Response::bytes(200, "image/jpeg");
            response.body = poster.clone();
            fit.apply(&mut response);
            assert_eq!(fit.cached().unwrap(), (response.body.clone(), response.content_type.clone()));
            response
        };
        let ask = |pairs: &[(&str, &str)]| {
            let response = fit(pairs);
            let (width, height, _) = super::super::blur::decode(&response.body).unwrap();
            (width, height, response.body.len())
        };
        // Plex Web's grid: covers 240x360, which a 2:3 poster fills exactly.
        let (width, height, size) = ask(&[("width", "240"), ("height", "360"), ("minSize", "1"), ("upscale", "1")]);
        assert_eq!((width, height), (240, 360));
        assert!(size * 10 < poster.len(), "{size} of {}", poster.len());
        // Fit inside a wide box: the height decides.
        assert_eq!(ask(&[("width", "1000"), ("height", "300")]).0, 200);
        // Fit inside a box the picture already fits in: it does not grow.
        let (width, height, _) = ask(&[("width", "2000"), ("height", "2000"), ("minSize", "1")]);
        assert_eq!((width, height), (1920, 2880));
        // `format=png` is answered as PNG, as a real PMS answers it.
        let png = fit(&[("width", "100"), ("height", "150"), ("format", "png")]);
        assert_eq!(png.content_type, "image/png");
        assert!(png.body.starts_with(b"\x89PNG"));
    }
}
