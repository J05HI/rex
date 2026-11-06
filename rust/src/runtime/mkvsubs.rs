//! A release's text subtitle track, read without reading the release: a Matroska one
//! through its Cues, an MP4 one through its sample tables (`mp4subs`).
//!
//! Burning a text subtitle takes FFmpeg's `subtitles` filter, which reads its whole file
//! before the first frame: pointed at the release itself that is a full download (30-67 GB
//! for the remuxes on the production server). mkvmerge indexes every subtitle block in the
//! Cues with its exact place in its Cluster (`CueRelativePosition`, and `CueDuration`):
//! measured on two real releases, a WEB-DL's one SRT (1,468 cues) and a remux's two SRTs and
//! seven PGS tracks, every block of every subtitle track. So the track is read block by block
//! with Range requests, a few hundred bytes each, one at a time (`Index::events`).

use std::sync::atomic::{AtomicBool, Ordering};

const SEGMENT: u64 = 0x1853_8067;
const SEEK_HEAD: u64 = 0x114D_9B74;
const SEEK: u64 = 0x4DBB;
const SEEK_ID: u64 = 0x53AB;
const SEEK_POSITION: u64 = 0x53AC;
const INFO: u64 = 0x1549_A966;
const TIMESTAMP_SCALE: u64 = 0x2A_D7B1;
const TRACKS: u64 = 0x1654_AE6B;
const TRACK_ENTRY: u64 = 0xAE;
const TRACK_NUMBER: u64 = 0xD7;
const TRACK_TYPE: u64 = 0x83;
const CODEC_ID: u64 = 0x86;
const CODEC_PRIVATE: u64 = 0x63A2;
const CONTENT_ENCODINGS: u64 = 0x6D80;
const CONTENT_ENCODING: u64 = 0x6240;
const CONTENT_ENCODING_TYPE: u64 = 0x5033;
const CONTENT_COMPRESSION: u64 = 0x5034;
const CONTENT_COMP_ALGO: u64 = 0x4254;
const CONTENT_COMP_SETTINGS: u64 = 0x4255;
const CUES: u64 = 0x1C53_BB6B;
const CUE_POINT: u64 = 0xBB;
const CUE_TIME: u64 = 0xB3;
const CUE_TRACK_POSITIONS: u64 = 0xB7;
const CUE_TRACK: u64 = 0xF7;
const CUE_CLUSTER_POSITION: u64 = 0xF1;
const CUE_RELATIVE_POSITION: u64 = 0xF0;
const CUE_DURATION: u64 = 0xB2;
const BLOCK_GROUP: u64 = 0xA0;
const BLOCK: u64 = 0xA1;
const BLOCK_DURATION: u64 = 0x9B;
const SIMPLE_BLOCK: u64 = 0xA3;

/// What a block of the track is read with: `bytes(offset, length)` of the release.
pub(crate) type Read<'a> = &'a (dyn Fn(u64, usize) -> Result<Vec<u8>, String> + Sync);

/// The subtitle track and where each of its blocks is.
#[derive(Debug)]
pub(crate) struct Index {
    pub(super) number: u64,
    /// The Matroska CodecID, or `tx3g` for an MP4's timed text.
    pub(super) codec: String,
    /// An ASS/SSA track's script header: its styles and the `[Events]` format.
    pub(super) private: Vec<u8>,
    pub(super) compression: Compression,
    /// Nanoseconds per tick (`TimestampScale`).
    pub(super) scale: u64,
    pub cues: Vec<Cue>,
}

#[derive(Debug, PartialEq)]
pub(super) enum Compression {
    None,
    Zlib,
    /// Header stripping: these bytes were removed from the front of every block.
    Strip(Vec<u8>),
}

#[derive(Clone, Debug)]
pub(crate) struct Cue {
    /// Milliseconds on the title's clock.
    pub start: i64,
    pub(super) duration: Option<i64>,
    pub(super) place: Place,
}

#[derive(Clone, Debug, PartialEq)]
pub(super) enum Place {
    /// A Matroska block: its Cluster's absolute offset and its offset in the Cluster's data.
    Block { cluster: u64, relative: u64 },
    /// An MP4 sample: where it is and how long.
    Sample { offset: u64, size: usize },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Event {
    pub start: i64,
    pub duration: Option<i64>,
    pub text: String,
}

/// An EBML variable-length integer at `at`: its value (with its length marker for an ID)
/// and where it ends. An all-ones size is "unknown", read as `u64::MAX`.
fn vint(bytes: &[u8], at: usize, id: bool) -> Option<(u64, usize)> {
    let first = *bytes.get(at)?;
    if first == 0 {
        return None;
    }
    let length = first.leading_zeros() as usize + 1;
    let tail = bytes.get(at + 1..at + length)?;
    let marker = 0x80u64 >> (length - 1);
    let mut value = if id { first as u64 } else { first as u64 & (marker - 1) };
    let mut ones = !id && value == marker - 1;
    for byte in tail {
        value = (value << 8) | *byte as u64;
        ones &= *byte == 0xFF;
    }
    Some((if ones { u64::MAX } else { value }, at + length))
}

/// The elements between `start` and `end`: id, data start, data end (which may lie past
/// the bytes held, for an element only partly read).
fn children(bytes: &[u8], start: usize, end: usize) -> Vec<(u64, usize, usize)> {
    let mut out = Vec::new();
    let mut at = start;
    let end = end.min(bytes.len());
    while at < end {
        let Some((id, data)) = vint(bytes, at, true) else { break };
        let Some((size, data)) = vint(bytes, data, false) else { break };
        let stop = if size == u64::MAX { end } else { data.saturating_add(size as usize) };
        out.push((id, data, stop));
        at = stop;
    }
    out
}

fn uint(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0, |value, byte| (value << 8) | *byte as u64)
}

fn child<'a>(bytes: &'a [u8], start: usize, end: usize, id: u64) -> Option<&'a [u8]> {
    children(bytes, start, end).into_iter().find(|entry| entry.0 == id).and_then(|(_, a, e)| bytes.get(a..e))
}

/// Where the track behind ffprobe's stream `stream` keeps its blocks. ffprobe numbers a
/// Matroska file's streams in `TrackEntry` order, attachments after them.
pub(crate) fn index(read: Read, stream: i64) -> Result<Index, String> {
    let head = read(0, 64 * 1024)?;
    if !head.starts_with(&[0x1A, 0x45, 0xDF, 0xA3]) {
        return super::mp4subs::index(read, stream);
    }
    let (segment, info, tracks, cues_bytes) = matroska_tables(read, &head)?;
    let scale = child(&info, 0, info.len(), TIMESTAMP_SCALE).map(uint).filter(|scale| *scale > 0).unwrap_or(1_000_000);
    subtitle_index(stream, segment, scale, &tracks, &cues_bytes)
}

/// Read only the seek tables, never the media clusters.
fn matroska_tables(read: Read, head: &[u8]) -> Result<(usize, Vec<u8>, Vec<u8>, Vec<u8>), String> {
    let (_, segment, segment_end) = children(&head, 0, head.len())
        .into_iter()
        .find(|entry| entry.0 == SEGMENT)
        .ok_or("no Matroska segment")?;
    let mut seeks = Vec::new();
    for (id, start, end) in children(&head, segment, segment_end) {
        if id == SEEK_HEAD && end <= head.len() {
            for (_, seek, seek_end) in children(&head, start, end).into_iter().filter(|entry| entry.0 == SEEK) {
                let id = child(&head, seek, seek_end, SEEK_ID).map(uint);
                let position = child(&head, seek, seek_end, SEEK_POSITION).map(uint);
                if let (Some(id), Some(position)) = (id, position) {
                    seeks.push((id, segment as u64 + position));
                }
            }
            break;
        }
    }
    let element = |wanted: u64| -> Result<Vec<u8>, String> {
        let at = seeks.iter().find(|seek| seek.0 == wanted).map(|seek| seek.1).ok_or_else(|| format!("no SeekHead entry {wanted:x}"))?;
        // Reuse the head, or fetch the element's header and opening payload together.
        let cached = usize::try_from(at).ok().and_then(|start| head.get(start..)).filter(|bytes| bytes.len() >= 12);
        let fetched;
        let bytes = match cached {
            Some(bytes) => bytes,
            None => { fetched = read(at, 64 * 1024)?; &fetched }
        };
        let (_, data) = vint(bytes, 0, true).ok_or("bad element id")?;
        let (size, data) = vint(bytes, data, false).ok_or("bad element size")?;
        if size > 64 * 1024 * 1024 {
            return Err(format!("element {wanted:x} of {size} bytes"));
        }
        let size = size as usize;
        let held = bytes.len().saturating_sub(data).min(size);
        let mut payload = bytes[data..data + held].to_vec();
        if held < size {
            payload.extend(read(at + (data + held) as u64, size - held)?);
        }
        if payload.len() != size { return Err("truncated Matroska table".into()); }
        Ok(payload)
    };
    let info = element(INFO)?;
    let tracks = element(TRACKS)?;
    Ok((segment, info, tracks, element(CUES)?))
}

/// A conservative upper bound on the spacing of indexed video keyframes, in ms.
/// Sparse cues make segments longer, rather than claiming a cut the video cannot make.
pub(crate) fn video_gap(read: Read, stream: i64, duration_ms: i64) -> Result<i64, String> {
    let head = read(0, 64 * 1024)?;
    if !head.starts_with(&[0x1A, 0x45, 0xDF, 0xA3]) {
        return Err("not Matroska".to_string());
    }
    let (_, info, tracks, cues) = matroska_tables(read, &head)?;
    let scale = child(&info, 0, info.len(), TIMESTAMP_SCALE).map(uint).filter(|scale| *scale > 0).unwrap_or(1_000_000);
    let (_, start, end) = children(&tracks, 0, tracks.len()).into_iter()
        .filter(|entry| entry.0 == TRACK_ENTRY)
        .nth(usize::try_from(stream).map_err(|_| "negative stream index")?).ok_or("no video track")?;
    if child(&tracks, start, end, TRACK_TYPE).map(uint) != Some(1) {
        return Err("not a video track".to_string());
    }
    let number = child(&tracks, start, end, TRACK_NUMBER).map(uint).ok_or("track without a number")?;
    let mut times = Vec::new();
    for (_, start, end) in children(&cues, 0, cues.len()).into_iter().filter(|entry| entry.0 == CUE_POINT) {
        let video = children(&cues, start, end).into_iter().filter(|entry| entry.0 == CUE_TRACK_POSITIONS)
            .any(|(_, a, e)| child(&cues, a, e, CUE_TRACK).map(uint) == Some(number));
        if video {
            let ticks = child(&cues, start, end, CUE_TIME).map(uint).ok_or("cue without a timestamp")?;
            let ms = i64::try_from(ticks as u128 * scale as u128 / 1_000_000).map_err(|_| "cue timestamp overflow")?;
            if ms >= duration_ms { continue; }
            times.push(ms);
        }
    }
    if times.is_empty() { return Err("no video cues".to_string()); }
    let opening = times.iter().copied().min().unwrap_or(0);
    times.extend([0, duration_ms]);
    times.sort_unstable();
    times.windows(2).map(|pair| pair[1] - pair[0]).max().unwrap_or(0)
        .checked_add(opening).ok_or_else(|| "video gap overflow".to_string())
}

fn subtitle_index(stream: i64, segment: usize, scale: u64, tracks: &[u8], cues_bytes: &[u8]) -> Result<Index, String> {
    let (_, start, end) = children(&tracks, 0, tracks.len())
        .into_iter()
        .filter(|entry| entry.0 == TRACK_ENTRY)
        .nth(usize::try_from(stream).map_err(|_| "negative stream index")?)
        .ok_or_else(|| format!("no track for stream {stream}"))?;
    let number = child(&tracks, start, end, TRACK_NUMBER).map(uint).ok_or("track without a number")?;
    let codec = child(&tracks, start, end, CODEC_ID).map(|bytes| String::from_utf8_lossy(bytes).to_string()).unwrap_or_default();
    if child(&tracks, start, end, TRACK_TYPE).map(uint) != Some(17) || !matches!(codec.as_str(), "S_TEXT/UTF8" | "S_TEXT/ASS" | "S_TEXT/SSA") {
        return Err(format!("stream {stream} is {codec}, not a text subtitle"));
    }
    let private = child(&tracks, start, end, CODEC_PRIVATE).unwrap_or_default().to_vec();
    let mut compression = Compression::None;
    if let Some(encodings) = child(&tracks, start, end, CONTENT_ENCODINGS) {
        for (_, a, e) in children(encodings, 0, encodings.len()).into_iter().filter(|entry| entry.0 == CONTENT_ENCODING) {
            if child(encodings, a, e, CONTENT_ENCODING_TYPE).map(uint).unwrap_or(0) != 0 {
                return Err("an encrypted track".to_string());
            }
            if let Some(compressed) = child(encodings, a, e, CONTENT_COMPRESSION) {
                compression = match child(compressed, 0, compressed.len(), CONTENT_COMP_ALGO).map(uint).unwrap_or(0) {
                    0 => Compression::Zlib,
                    3 => Compression::Strip(child(compressed, 0, compressed.len(), CONTENT_COMP_SETTINGS).unwrap_or_default().to_vec()),
                    other => return Err(format!("compression {other}")),
                };
            }
        }
    }
    let to_ms = |ticks: u64| (ticks as u128 * scale as u128 / 1_000_000) as i64;
    let mut cues = Vec::new();
    for (_, point, point_end) in children(&cues_bytes, 0, cues_bytes.len()).into_iter().filter(|entry| entry.0 == CUE_POINT) {
        let Some(time) = child(&cues_bytes, point, point_end, CUE_TIME).map(uint) else { continue };
        for (_, a, e) in children(&cues_bytes, point, point_end).into_iter().filter(|entry| entry.0 == CUE_TRACK_POSITIONS) {
            if child(&cues_bytes, a, e, CUE_TRACK).map(uint) != Some(number) {
                continue;
            }
            let (Some(cluster), Some(relative)) = (child(&cues_bytes, a, e, CUE_CLUSTER_POSITION).map(uint), child(&cues_bytes, a, e, CUE_RELATIVE_POSITION).map(uint)) else {
                continue;
            };
            let duration = child(&cues_bytes, a, e, CUE_DURATION).map(uint).map(to_ms);
            cues.push(Cue { start: to_ms(time), duration, place: Place::Block { cluster: segment as u64 + cluster, relative } });
        }
    }
    if cues.is_empty() {
        return Err(format!("the Cues place no block of track {number}"));
    }
    cues.sort_by_key(|cue| cue.start);
    let mut seen = std::collections::HashSet::new();
    cues.retain(|cue| seen.insert(format!("{:?}", cue.place)));
    Ok(Index { number, codec, private, compression, scale, cues })
}

impl Index {
    pub fn ass(&self) -> bool {
        matches!(self.codec.as_str(), "S_TEXT/ASS" | "S_TEXT/SSA")
    }

    /// One block's text. `CueRelativePosition` counts from the Cluster's data, whose header
    /// is 5 to 12 bytes; each length is tried and the block must name this track.
    fn block(&self, read: Read, cue: &Cue) -> Result<Event, String> {
        let (cluster, relative) = match cue.place {
            Place::Block { cluster, relative } => (cluster, relative),
            Place::Sample { offset, size } => {
                // A 3GPP timed-text sample: the text's length, the text, then style boxes.
                let bytes = read(offset, size)?;
                let length = bytes.get(..2).map_or(0, |len| u16::from_be_bytes([len[0], len[1]]) as usize);
                let text = bytes.get(2..2 + length).ok_or_else(|| format!("a {size} byte sample at {offset} holds no {length} bytes of text"))?;
                return Ok(Event { start: cue.start, duration: cue.duration, text: String::from_utf8_lossy(text).to_string() });
            }
        };
        let at = cluster + relative + 5;
        let mut bytes = read(at, 7 + 4096)?;
        for skip in 0..8 {
            let Some((id, data)) = vint(&bytes, skip, true) else { continue };
            let Some((size, data)) = vint(&bytes, data, false) else { continue };
            if !matches!(id, BLOCK_GROUP | SIMPLE_BLOCK) || size > 1024 * 1024 {
                continue;
            }
            let end = data + size as usize;
            if end > bytes.len() {
                // A long ASS line (a drawing) runs past the first read.
                let mut whole = read(at + skip as u64, end - skip)?;
                whole.splice(0..0, std::iter::repeat_n(0, skip));
                bytes = whole;
            }
            let (block, duration) = if id == SIMPLE_BLOCK {
                // A block that runs past what the source returned (a file cut short) is not
                // this cue's: indexing it panicked the reader and stalled the conversion.
                let Some(block) = bytes.get(data..end) else { continue };
                (block, None)
            } else {
                let Some(block) = child(&bytes, data, end, BLOCK) else { continue };
                (block, child(&bytes, data, end, BLOCK_DURATION).map(uint))
            };
            let Some((track, payload)) = vint(block, 0, false) else { continue };
            // Track number, a 16-bit timecode, flags: no lacing for a subtitle.
            if track != self.number || block.len() < payload + 3 || block[payload + 2] & 0x06 != 0 {
                continue;
            }
            let payload = &block[payload + 3..];
            let text = match &self.compression {
                Compression::None => payload.to_vec(),
                Compression::Strip(prefix) => [prefix.as_slice(), payload].concat(),
                Compression::Zlib => inflate(payload)?,
            };
            let text = String::from_utf8_lossy(&text).trim_end_matches('\0').to_string();
            let duration = duration.map(|ticks| (ticks as u128 * self.scale as u128 / 1_000_000) as i64);
            return Ok(Event { start: cue.start, duration: cue.duration.or(duration), text });
        }
        Err(format!("no block of track {} at {at}", self.number))
    }

    /// The text of `cues`, one read at a time, the blocks that could not be found, and whether
    /// the source refused a read. A refusal ends the reading: zurg answers parallel reads of a
    /// Real-Debrid or TorBox file with 503s, and after three network errors holds the whole
    /// file off for 5 minutes, its playback with it (measured 2026-10-19: 16 at once, every
    /// read refused; one at a time beside a stream, none). The caller paces them.
    pub fn events(&self, read: Read, cues: &[Cue]) -> (Vec<Event>, usize, bool) {
        let refused = AtomicBool::new(false);
        let guarded = |offset: u64, length: usize| read(offset, length).inspect_err(|_| refused.store(true, Ordering::SeqCst));
        let mut events = Vec::with_capacity(cues.len());
        let mut failed = 0;
        for (number, cue) in cues.iter().enumerate() {
            match self.block(&guarded, cue) {
                Ok(event) => events.push(event),
                Err(_) if refused.load(Ordering::SeqCst) => {
                    failed += cues.len() - number;
                    break;
                }
                Err(_) => failed += 1,
            }
        }
        // An MP4 track's empty samples are its gaps.
        events.retain(|event| !event.text.trim().is_empty());
        events.sort_by_key(|event| event.start);
        (events, failed, refused.into_inner())
    }

    /// The subtitle file FFmpeg's `subtitles` filter reads: SRT for `S_TEXT/UTF8`, the
    /// track's own ASS script otherwise. An event with no known duration lasts until the next.
    pub fn render(&self, events: &[Event]) -> String {
        let end = |index: usize| {
            let event = &events[index];
            match event.duration.filter(|duration| *duration > 0) {
                Some(duration) => event.start + duration,
                None => events.get(index + 1).map_or(event.start + 4000, |next| next.start.min(event.start + 8000)),
            }
        };
        if !self.ass() {
            let stamp = |ms: i64| format!("{:02}:{:02}:{:02},{:03}", ms / 3_600_000, ms / 60_000 % 60, ms / 1000 % 60, ms % 1000);
            return events
                .iter()
                .enumerate()
                .map(|(index, event)| format!("{}\n{} --> {}\n{}\n\n", index + 1, stamp(event.start), stamp(end(index)), event.text.replace("\r\n", "\n").trim_end()))
                .collect();
        }
        let stamp = |ms: i64| format!("{}:{:02}:{:02}.{:02}", ms / 3_600_000, ms / 60_000 % 60, ms / 1000 % 60, ms % 1000 / 10);
        let mut out = String::from_utf8_lossy(&self.private).trim_end().to_string();
        if !out.contains("[Events]") {
            out.push_str("\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text");
        }
        out.push('\n');
        for (index, event) in events.iter().enumerate() {
            // A block is `ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text`
            // (SSA's second field is `Marked`); the file's Dialogue drops ReadOrder and
            // puts the times after the layer.
            let mut fields = event.text.splitn(3, ',');
            let (_, layer, rest) = (fields.next(), fields.next().unwrap_or("0"), fields.next().unwrap_or(""));
            out.push_str(&format!("Dialogue: {layer},{},{},{rest}\n", stamp(event.start), stamp(end(index))));
        }
        out
    }
}

/// A zlib-compressed block's text, at most the 1 MiB a block may be: a 1 MiB block can
/// inflate to a gigabyte.
fn inflate(payload: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    std::io::Read::read_to_end(&mut std::io::Read::take(flate2::read::ZlibDecoder::new(payload), 1 << 20), &mut out)
        .map_err(|error| error.to_string())?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_compressed_block_inflates_to_at_most_a_block() {
        let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::best());
        std::io::Write::write_all(&mut encoder, &vec![b'a'; 64 << 20]).unwrap();
        let bomb = encoder.finish().unwrap();
        assert!(bomb.len() < 1 << 20);
        assert_eq!(inflate(&bomb).unwrap().len(), 1 << 20);
    }

    #[test]
    fn a_block_running_past_the_bytes_returned_is_no_block() {
        // A SimpleBlock of track 1 whose size (32) runs past the nine bytes a source cut short
        // returns, re-read included: it was sliced unchecked and the reader panicked.
        let file = [0, 0, 0, 0, 0, 0xA3, 0x40, 0x20, 0x81, 0, 0, 0, b'h', b'i'];
        let short = |offset: u64, length: usize| -> Result<Vec<u8>, String> {
            let start = (offset as usize).min(file.len());
            Ok(file[start..(start + length).min(file.len())].to_vec())
        };
        let index = Index { number: 1, codec: "S_TEXT/UTF8".to_string(), private: Vec::new(), compression: Compression::None, scale: 1_000_000, cues: Vec::new() };
        let cue = Cue { start: 0, duration: None, place: Place::Block { cluster: 0, relative: 0 } };
        assert!(index.block(&short, &cue).is_err());
    }

    /// A release generated by mkvmerge v103.0 (the muxer every release measured was made
    /// with): `tests/fixtures/transcode/mkvmerge_subtitles.mkv`, 1 s of 64x36 H.264 with an
    /// SRT, the same SRT zlib-compressed and an ASS track with two styles:
    /// `mkvmerge -o mkvmerge_subtitles.mkv v.mkv a.srt --compression 0:zlib a.srt c.ass`.
    const RELEASE: &[u8] = include_bytes!("../../../tests/fixtures/transcode/mkvmerge_subtitles.mkv");

    fn read(offset: u64, length: usize) -> Result<Vec<u8>, String> {
        let start = (offset as usize).min(RELEASE.len());
        Ok(RELEASE[start..(start + length).min(RELEASE.len())].to_vec())
    }

    #[test]
    fn video_spacing_includes_the_tail_and_ignores_subtitle_cues() {
        // This one-second release has video cues at zero and 500 ms, and three
        // subtitle cues. None of those subtitle timestamps can shorten the video gap.
        assert_eq!(video_gap(&read, 0, 1000).unwrap(), 500);
        assert_eq!(video_gap(&read, 0, 2000).unwrap(), 1500);
        assert!(video_gap(&read, 1, 1000).is_err());
        assert!(video_gap(&read, -1, 1000).is_err());
        assert!(video_gap(&read, 0, 0).is_err());
    }

    #[test]
    fn seek_tables_reuse_the_initial_read_and_fetch_only_missing_bytes() {
        let expected = matroska_tables(&read, RELEASE).unwrap();
        let no_read = |_, _| Err("unexpected source request".to_string());
        assert_eq!(matroska_tables(&no_read, RELEASE).unwrap(), expected);
        let requests = std::sync::atomic::AtomicUsize::new(0);
        let counted_read = |offset, length| {
            requests.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            read(offset, length)
        };
        assert_eq!(matroska_tables(&counted_read, &RELEASE[..8192]).unwrap(), expected);
        assert_eq!(requests.load(std::sync::atomic::Ordering::Relaxed), 1);
    }

    #[test]
    fn seek_tables_larger_than_64k_or_partially_cached_fetch_missing_bytes() {
        let at = 9255u64;
        let total_size = 70_000usize;
        let header = [0x1C, 0x53, 0xBB, 0x6B, 0x21, 0x11, 0x70];
        let mut expected_payload = Vec::with_capacity(total_size);
        for i in 0..total_size {
            expected_payload.push((i % 251) as u8);
        }
        let requests = std::sync::Mutex::new(Vec::new());
        let large_read = |offset: u64, length: usize| -> Result<Vec<u8>, String> {
            requests.lock().unwrap().push((offset, length));
            if offset == at {
                let mut data = header.to_vec();
                let opening_len = length.saturating_sub(header.len()).min(total_size);
                data.extend_from_slice(&expected_payload[..opening_len]);
                Ok(data)
            } else if offset == at + (header.len() + (64 * 1024 - header.len())) as u64 {
                let remaining_start = 64 * 1024 - header.len();
                Ok(expected_payload[remaining_start..].to_vec())
            } else {
                read(offset, length)
            }
        };
        let (_, _, _, cues_bytes) = matroska_tables(&large_read, &RELEASE[..8192]).unwrap();
        assert_eq!(cues_bytes.len(), total_size);
        assert_eq!(cues_bytes, expected_payload);
        let reqs = requests.lock().unwrap().clone();
        assert_eq!(reqs.len(), 2);
        assert_eq!(reqs[0], (at, 64 * 1024));
        assert_eq!(reqs[1], (at + 64 * 1024, total_size - (64 * 1024 - header.len())));

        let cues_in_head = 9255;
        let mut head_with_cues = RELEASE[..cues_in_head].to_vec();
        head_with_cues.extend_from_slice(&header);
        head_with_cues.extend_from_slice(&expected_payload[..50]);
        assert!(head_with_cues.len() >= cues_in_head + 12);
        requests.lock().unwrap().clear();
        let cached_partial_read = |offset: u64, length: usize| -> Result<Vec<u8>, String> {
            requests.lock().unwrap().push((offset, length));
            if offset == (cues_in_head + header.len() + 50) as u64 {
                Ok(expected_payload[50..].to_vec())
            } else {
                read(offset, length)
            }
        };
        let (_, _, _, cues_cached) = matroska_tables(&cached_partial_read, &head_with_cues).unwrap();
        assert_eq!(cues_cached.len(), total_size);
        assert_eq!(cues_cached, expected_payload);
        let reqs_cached = requests.lock().unwrap().clone();
        assert_eq!(reqs_cached.len(), 1);
        assert_eq!(reqs_cached[0], ((cues_in_head + header.len() + 50) as u64, total_size - 50));
    }

    #[test]
    fn an_srt_track_is_read_block_by_block_from_its_cues() {
        let index = index(&read, 1).unwrap();
        assert!(!index.ass());
        assert_eq!(index.cues.len(), 3);
        let (events, failed, _) = index.events(&read, &index.cues);
        assert_eq!(failed, 0);
        assert_eq!(
            index.render(&events),
            "1\n00:00:00,100 --> 00:00:00,400\nFirst line\n\n2\n00:00:00,500 --> 00:00:00,700\nSecond\nline\n\n3\n00:00:00,800 --> 00:00:00,950\nThird, last\n\n"
        );
    }

    #[test]
    fn a_compressed_track_is_inflated() {
        let index = index(&read, 2).unwrap();
        assert_eq!(index.compression, Compression::Zlib);
        let (events, failed, _) = index.events(&read, &index.cues);
        assert_eq!(failed, 0);
        assert_eq!(events.iter().map(|event| event.text.as_str()).collect::<Vec<_>>(), ["First line", "Second\r\nline", "Third, last"]);
    }

    #[test]
    fn an_ass_track_keeps_its_script_header_and_styles() {
        let index = index(&read, 3).unwrap();
        assert!(index.ass());
        let (events, _, _) = index.events(&read, &index.cues);
        let script = index.render(&events);
        assert!(script.contains("Style: Big,"), "{script}");
        assert!(script.contains("\nDialogue: 0,0:00:00.10,0:00:00.40,Big,,0,0,0,,{\\an8}Top line\n"), "{script}");
        assert!(script.ends_with("Dialogue: 1,0:00:00.50,0:00:00.90,Default,,0,0,0,,Comma, kept\n"), "{script}");
    }

    #[test]
    fn a_stream_that_is_not_text_is_refused() {
        assert!(index(&read, 0).unwrap_err().contains("not a text subtitle"));
        assert!(index(&read, 9).is_err());
    }

    #[test]
    fn a_refused_read_ends_the_reading() {
        let index = index(&read, 1).unwrap();
        let reads = std::sync::atomic::AtomicUsize::new(0);
        let refusing = |offset: u64, length: usize| {
            if reads.fetch_add(1, Ordering::SeqCst) == 1 {
                return Err("the source answered 503 to a range".to_string());
            }
            read(offset, length)
        };
        let (events, failed, refused) = index.events(&refusing, &index.cues);
        assert!(refused);
        assert_eq!((events.len(), failed), (1, 2));
        assert_eq!(reads.load(Ordering::SeqCst), 2, "no read after the refusal");
    }
}
