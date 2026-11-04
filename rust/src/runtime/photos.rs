//! Photos: what a source pushes as `kind: "photo"` (docs/feed-api.md, "Photos"), as a real PMS's
//! photo library shows a folder of pictures -- each folder an album (type 14), each picture a photo
//! (type 13), both `type="photo"` on the wire, the album a `Directory` and the picture a `Photo`.
//!
//! rex identifies nothing here either, and reads no picture: a photo is titled as its file (the
//! file name without its extension), an album as its folder, exactly as named, and the folders of
//! the item's `path` below its first directory are its albums -- what plexscratch's PMS 1.43.4 made
//! of `Pepper&Carrot/Episode 01 - Potion of Flight/page 1.jpg` in a library on that directory, and
//! of a folder named `Witch Test v01 (2019) (Digital).cbz`, extension and all (2026-11-04,
//! `tests/fixtures/photos/`). A source decides what its folders are: zurg lists a comic book's
//! pages under its archive's name. The picture's size comes from its source's probe when it sends
//! one; without, its Media has none, as no file is read.

use super::feed::{held_key, lock, Counts, Item};
use super::source::Source;
use super::*;

/// What a source may push as a photo: the formats a Plex client draws as they are, since rex
/// converts no picture (`/photo/:/transcode` answers the file itself).
const IMAGES: &[&str] = &["jpg", "jpeg", "png", "gif", "webp"];

pub(super) fn is_image(path: &str) -> bool {
    path.rsplit_once('.').is_some_and(|(_, extension)| IMAGES.contains(&extension.to_ascii_lowercase().as_str()))
}

/// PMS's container for a picture: `jpeg` for a `.jpg`, the extension otherwise (plexscratch: a
/// `.png` page is `png`).
fn container(extension: &str) -> String {
    match extension {
        "jpg" | "jpeg" => "jpeg".to_string(),
        other => other.to_string(),
    }
}

/// A title's sort key as a photo library orders it: case folded, a run of digits by its value, so
/// `page 2` comes before `page 10` (plexscratch's album listed `page 1`, `page 2`, `page 10`, made
/// in the order `page 1`, `page 10`, `page 2`).
pub(super) fn natural_key(title: &str) -> Vec<(u8, u128, String)> {
    let mut key = Vec::new();
    let mut chars = title.chars().peekable();
    while let Some(c) = chars.next() {
        if c.is_ascii_digit() {
            let mut digits = c.to_string();
            while let Some(d) = chars.next_if(char::is_ascii_digit) {
                digits.push(d);
            }
            key.push((0, digits.parse::<u128>().unwrap_or(u128::MAX), String::new()));
        } else {
            key.push((1, 0, c.to_lowercase().collect()));
        }
    }
    key
}

/// The albums an item sits in: the folders of its path below its first directory, the library's.
fn albums(item: &Item) -> Vec<String> {
    let segments: Vec<&str> = item.path.split('/').collect();
    match segments.len() {
        0..=2 => Vec::new(),
        n => segments[1..n - 1].iter().map(|segment| segment.to_string()).collect(),
    }
}

/// `createdAtTZOffset`: the server's offset from UTC at `stamp`, in seconds, as PMS stamps a picture
/// with no EXIF time (plexscratch, Europe/Amsterdam in November: `"3600"`).
fn tz_offset(stamp: i64) -> i64 {
    crate::platform::local_tm(stamp).map_or(0, |tm| {
        let days = super::edit::days_from_civil(tm.tm_year as i64 + 1900, tm.tm_mon as i64 + 1, tm.tm_mday as i64);
        days * 86_400 + tm.tm_hour as i64 * 3_600 + tm.tm_min as i64 * 60 + tm.tm_sec as i64 - stamp
    })
}

pub(super) fn upsert_photos(shared: &SharedRuntime, source: &Source, items: Vec<Item>, counts: &mut Counts, mut seen: Option<&mut HashSet<String>>, retry: bool) {
    let mut guard = lock(shared);
    let rows = guard.feed_rows(&source.name, items.iter().map(|item| item.id.as_str()));
    // Albums found or made in this push, by section and folder path: a comic of 200 pages asks once.
    let mut made: HashMap<(String, String), String> = HashMap::new();
    let mut created = false;
    for item in items {
        let Some(section) = guard.photo_library(&item) else {
            counts.rejected.push(json!({"id": item.id, "reason": format!("no photo library holds {}", item.file)}));
            continue;
        };
        if let Some(seen) = seen.as_deref_mut() {
            seen.insert(item.id.clone());
        }
        if retry {
            continue;
        }
        guard.release(&held_key(&source.name, &item.id));
        match rows.get(&item.id) {
            Some(key) if guard.catalog.get(key).and_then(|row| row.get("_path")).and_then(Value::as_str) == Some(item.path.as_str()) => {
                if guard.refresh_photo_part(key, &item) {
                    counts.updated += 1;
                } else {
                    counts.unchanged += 1;
                }
            }
            // Moved to other folders: the picture leaves its albums and is placed again on its key.
            Some(key) => {
                let key = key.clone();
                guard.unplace_photo(&key);
                guard.place_photo(&section, source, &item, Some(key), &mut made);
                counts.updated += 1;
            }
            None => {
                let key = guard.place_photo(&section, source, &item, None, &mut made);
                guard.push_created_frames(&key);
                counts.created += 1;
                created = true;
            }
        }
    }
    if created || counts.updated > 0 {
        guard.dirty = true;
        guard.touch_gdm();
    }
}

impl Runtime {
    pub(super) fn photo_section(&self, section: &str) -> bool {
        self.catalog.sections.iter().any(|row| {
            row.get("key").map(string_value).as_deref() == Some(section) && row.get("type").and_then(Value::as_str) == Some("photo")
        })
    }

    /// The photo library a picture goes to: the one its `library` names, else the one whose folder
    /// holds its file or names its first directory, else the first photo library. rex makes none on
    /// a push: Add Library (Photos) or a source's `directories` (`photos`) make one.
    pub(super) fn photo_library(&self, item: &Item) -> Option<String> {
        let photos: Vec<&Value> = self.catalog.sections.iter().filter(|section| section.get("type").and_then(Field::as_str) == Some("photo")).collect();
        let key = |section: &&Value| section.get("key").map(string_value);
        if let Some(wanted) = item.library.as_deref().filter(|wanted| *wanted != "photo") {
            return photos.iter().find(|section| key(section).as_deref() == Some(wanted)).and_then(key);
        }
        let directory = item.path.split('/').next().unwrap_or_default();
        photos
            .iter()
            .find(|section| self.locations(section).iter().any(|location| super::scan::under(&item.file, location)))
            .or_else(|| {
                photos.iter().find(|section| {
                    self.locations(section).iter().any(|location| location.trim_end_matches('/').rsplit('/').next() == Some(directory))
                })
            })
            .or(photos.first())
            .and_then(key)
    }

    fn refresh_photo_part(&mut self, key: &str, item: &Item) -> bool {
        let Some(part) = self.catalog.get_mut(key).and_then(|row| row.pointer_mut("/Media/0/Part/0")).and_then(Value::as_object_mut) else {
            return false;
        };
        let moved = part.get("file").and_then(Value::as_str) != Some(item.file.as_str());
        let resized = item.size > 0 && part.get("size").and_then(Value::as_u64) != Some(item.size);
        if moved {
            part.insert("file".to_string(), json!(item.file));
        }
        if resized {
            part.insert("size".to_string(), json!(item.size));
        }
        self.dirty |= moved || resized;
        moved || resized
    }

    /// The album of `path` (its folders, joined) in `section` under `parent`, found or made.
    fn photo_album(&mut self, section: &str, source: &Source, folders: &[String], parent: Option<&str>, made: &mut HashMap<(String, String), String>) -> String {
        let identity = format!("{}:{}", source.name, folders.join("/"));
        if let Some(key) = made.get(&(section.to_string(), identity.clone())).filter(|key| self.catalog.get(key).is_some()) {
            return key.clone();
        }
        let found = self
            .catalog
            .find_where(|row| {
                row.get("_photoAlbum").and_then(Field::as_str) == Some(identity.as_str())
                    && string_value(row.get("librarySectionID").unwrap_or(Field::Null)) == section
            })
            .and_then(key_of);
        let key = found.unwrap_or_else(|| {
            let stamp = now();
            let rk = self.mint_key();
            let title = folders.last().cloned().unwrap_or_default();
            let mut row = Map::new();
            row.insert("ratingKey".to_string(), json!(rk));
            row.insert("key".to_string(), json!(format!("/library/metadata/{rk}/children")));
            if let Some(parent) = parent {
                row.insert("parentRatingKey".to_string(), json!(parent));
            }
            row.insert("guid".to_string(), json!(format!("local://{rk}")));
            row.insert("type".to_string(), json!("photo"));
            row.insert("title".to_string(), json!(title));
            if let Some(parent) = parent {
                row.insert("parentKey".to_string(), json!(format!("/library/metadata/{parent}")));
            }
            row.insert("summary".to_string(), json!(""));
            row.insert("index".to_string(), json!(1));
            row.insert("composite".to_string(), json!(format!("/library/metadata/{rk}/composite/{stamp}")));
            row.insert("thumb".to_string(), json!(format!("/library/metadata/{rk}/thumb/{stamp}")));
            row.insert("art".to_string(), json!(format!("/library/metadata/{rk}/art/{stamp}")));
            row.insert("addedAt".to_string(), json!(stamp));
            row.insert("updatedAt".to_string(), json!(stamp));
            row.insert("_element".to_string(), json!("Directory"));
            row.insert("_photoAlbum".to_string(), json!(identity));
            row.insert("_source".to_string(), json!(source.name));
            self.photo_library_fields(section, &mut row);
            self.catalog.add(Value::Object(row));
            info!("FEED {} album {rk} {:?}", source.name, folders.join("/"));
            rk
        });
        made.insert((section.to_string(), identity), key.clone());
        key
    }

    fn photo_library_fields(&self, section: &str, row: &mut Map<String, Value>) {
        let title = self
            .catalog
            .sections
            .iter()
            .find(|row| row.get("key").map(string_value).as_deref() == Some(section))
            .and_then(|row| row.get("title"))
            .map(string_value)
            .unwrap_or_default();
        row.insert("librarySectionID".to_string(), section.parse::<i64>().map(Value::from).unwrap_or_else(|_| json!(section)));
        row.insert("librarySectionTitle".to_string(), json!(title));
        row.insert("librarySectionKey".to_string(), json!(format!("/library/sections/{section}")));
        row.insert("_scanned".to_string(), json!(true));
    }

    /// A picture's albums, each found or made, and its own row, on `key` when it keeps one.
    fn place_photo(&mut self, section: &str, source: &Source, item: &Item, key: Option<String>, made: &mut HashMap<(String, String), String>) -> String {
        let folders = albums(item);
        let mut parent: Option<String> = None;
        let mut chain: Vec<String> = Vec::new();
        for depth in 1..=folders.len() {
            let album = self.photo_album(section, source, &folders[..depth], parent.as_deref(), made);
            chain.push(album.clone());
            parent = Some(album);
        }
        let stamp = now();
        let key = key.unwrap_or_else(|| self.mint_key());
        let rating = key.parse::<i64>().unwrap_or(0);
        let file_name = item.file.rsplit('/').next().unwrap_or_default().to_string();
        let extension = super::scan::extension(&file_name).to_ascii_lowercase();
        let title = item
            .display
            .as_ref()
            .and_then(|display| display.get("title")?.as_str())
            .map(str::to_string)
            .unwrap_or_else(|| file_name.rsplit_once('.').map_or(file_name.as_str(), |(stem, _)| stem).to_string());
        // A picture with no EXIF time is dated by its file: here, when it arrived, unless its
        // source dates it (`display.date`).
        let date = item
            .display
            .as_ref()
            .and_then(|display| display.get("date")?.as_str())
            .filter(|date| date.len() == 10)
            .map(str::to_string)
            .unwrap_or_else(|| super::edit::civil_from_days((stamp + tz_offset(stamp)).div_euclid(86_400)));
        let part_id = rating * 10;
        let mut media = Map::new();
        media.insert("id".to_string(), json!(rating * 100));
        // ffprobe reads a picture as one video stream of its size.
        let stream = item.media_info.as_ref().and_then(|probe| probe.get("streams")?.as_array()?.iter().find(|s| s.get("width").is_some()).cloned());
        if let Some(stream) = stream {
            let (width, height) = (stream.get("width").and_then(Value::as_i64), stream.get("height").and_then(Value::as_i64));
            if let (Some(width), Some(height)) = (width, height) {
                media.insert("width".to_string(), json!(width));
                media.insert("height".to_string(), json!(height));
                if let Some(ratio) = crate::mediainfo::bucket_aspect(width as f64, height as f64) {
                    media.insert("aspectRatio".to_string(), json!(ratio));
                }
            }
        }
        media.insert("container".to_string(), json!(container(&extension)));
        media.insert(
            "Part".to_string(),
            json!([{
                "id": part_id, "key": format!("/library/parts/{part_id}/{stamp}/file.{extension}"), "file": item.file,
                "size": item.size, "container": container(&extension),
            }]),
        );
        let mut row = Map::new();
        row.insert("ratingKey".to_string(), json!(key));
        row.insert("key".to_string(), json!(format!("/library/metadata/{key}")));
        if let Some(parent) = &parent {
            row.insert("parentRatingKey".to_string(), json!(parent));
        }
        row.insert("guid".to_string(), json!(format!("local://{key}")));
        row.insert("type".to_string(), json!("photo"));
        row.insert("title".to_string(), json!(title));
        if let Some(parent) = &parent {
            row.insert("parentKey".to_string(), json!(format!("/library/metadata/{parent}")));
        }
        row.insert("summary".to_string(), json!(""));
        row.insert("index".to_string(), json!(1));
        row.insert("year".to_string(), json!(date[..4].parse::<i64>().unwrap_or(0)));
        row.insert("thumb".to_string(), json!(format!("/library/metadata/{key}/thumb/{stamp}")));
        row.insert("originallyAvailableAt".to_string(), json!(date));
        row.insert("addedAt".to_string(), json!(stamp));
        row.insert("updatedAt".to_string(), json!(stamp));
        row.insert("createdAtAccuracy".to_string(), json!("local"));
        row.insert("createdAtTZOffset".to_string(), json!(tz_offset(stamp).to_string()));
        row.insert("Media".to_string(), json!([media]));
        row.insert("_element".to_string(), json!("Photo"));
        for (field, value) in [("_source", json!(source.name)), ("_sid", json!(item.id)), ("_path", json!(item.path)), ("_group", json!(item.release))] {
            row.insert(field.to_string(), value);
        }
        self.photo_library_fields(section, &mut row);
        self.catalog.add(Value::Object(row));
        info!("FEED {} {} -> photo {key} in album {}", source.name, item.id, parent.as_deref().unwrap_or("-"));
        key
    }

    /// A picture taken out of its albums before it is placed again: the row goes, and an album it
    /// leaves with nothing goes too, up the chain (their keys retired).
    fn unplace_photo(&mut self, key: &str) {
        let Some(row) = self.catalog.remove(key) else {
            return;
        };
        let mut parent = row.get("parentRatingKey").map(string_value);
        while let Some(album) = parent {
            if !self.catalog.children_refs(&album).is_empty() {
                break;
            }
            parent = self.catalog.get(&album).and_then(|row| row.get("parentRatingKey")).map(string_value);
            if self.catalog.remove(&album).is_some() {
                self.deleted.insert(&album);
                if let Ok(number) = album.parse::<i64>() {
                    self.retired_keys.push(number);
                }
            }
        }
    }

    /// A photo album's first picture, depth first in its own order: what its cover, background
    /// and composite show (rex composes no mosaic; PMS's composite is one of four pictures).
    pub(super) fn album_cover(&self, album: &str) -> Option<Value> {
        let mut children: Vec<Value> = self.catalog.children_refs(album).iter().map(|row| (*row).clone()).collect();
        children.sort_by_key(|row| natural_key(row.get("title").and_then(Value::as_str).unwrap_or_default()));
        for child in children {
            if child.get("Media").is_some() {
                return Some(child);
            }
            if let Some(found) = child.get("ratingKey").map(string_value).and_then(|key| self.album_cover(&key)) {
                return Some(found);
            }
        }
        None
    }

    /// What a photo library's clients ask that rex answers as plexscratch did (Plex Web 4.160.0's
    /// walk, `tests/fixtures/photos/web-photos.jsonl`); `None` for anything else, which the
    /// ordinary routes answer.
    pub(super) fn route_photos(&mut self, request: &Request, path: &str, query: &HashMap<String, String>) -> Option<Response> {
        let get = matches!(request.method.as_str(), "GET" | "HEAD");
        let segments: Vec<&str> = path.trim_start_matches('/').split('/').collect();
        let photo = |this: &Self, key: &str| this.catalog.get(key).filter(|row| is_photo_row(row)).cloned();
        match (request.method.as_str(), segments.as_slice()) {
            (_, ["library", "sections", section, "all"]) if get && self.photo_section(section) => Some(self.photo_listing(section, query)),
            (_, ["library", "sections", section, "cluster"]) if get && self.photo_section(section) => Some(self.photo_clusters(section)),
            (_, ["hubs", "sections", section]) if get && self.photo_section(section) => Some(self.photo_hubs(section, query)),
            (_, ["library", "metadata", key]) if get => {
                let row = photo(self, key)?;
                let mut out = self.photo_full(&row);
                out.insert("Extras".to_string(), json!({"size": 0}));
                Some(Response::json(200, json!({"MediaContainer": self.photo_container(&row, vec![Value::Object(out)])})))
            }
            (_, ["library", "metadata", key, "children"]) if get => {
                let row = photo(self, key).filter(is_album)?;
                Some(self.album_children(&row, query))
            }
            (_, ["hubs", "metadata", key, "related"]) if get => {
                let row = photo(self, key)?;
                Some(Response::json(200, json!({"MediaContainer": self.photo_container(&row, Vec::new())})))
            }
            (_, ["library", "metadata", key, "thumb" | "art" | "composite", ..]) if get => {
                let row = photo(self, key)?;
                Some(self.photo_bytes(&row, request))
            }
            (_, [":", "timeline"]) => {
                let row = photo(self, query.get("ratingKey")?)?;
                // The queue's selected item follows the viewer (plexscratch: `/playQueues/8` named
                // page 2 once its timeline had).
                if let Some(item) = query.get("playQueueItemID").and_then(|id| id.parse::<i64>().ok()) {
                    if let Some(queue) = self.queues.values_mut().find(|queue| queue.items.iter().any(|it| it.id == item)) {
                        queue.selected = Some(item);
                    }
                }
                let _ = row;
                Some(Response::json(200, json!({"MediaContainer": {"size": 0, "playbackState": "ignore"}})))
            }
            (_, ["photo", ":", "transcode"]) if get => {
                let row = self.photo_of_url(query.get("url")?)?;
                Some(self.photo_bytes(&row, request))
            }
            ("POST", ["playQueues"]) if query.get("type").map(String::as_str) == Some("photo") => self.photo_queue(query),
            (_, ["playQueues", id]) if get => {
                let queue = self.queues.get(*id)?;
                let first = queue.items.first()?;
                photo(self, &first.rating_key)?;
                Some(self.photo_queue_answer(id))
            }
            _ => None,
        }
    }

    /// The photo row a `/photo/:/transcode` `url=` names: a Part's bytes or a row's
    /// `thumb`/`art`/`composite`, as Plex Web asks for its tiles and its viewer.
    fn photo_of_url(&self, url: &str) -> Option<Value> {
        let path = url.split('?').next()?;
        let segments: Vec<&str> = path.trim_start_matches('/').split('/').collect();
        let row = match segments.as_slice() {
            ["library", "parts", id, ..] => self.catalog.rows_by(crate::catalog::By::Part, id).into_iter().next().cloned(),
            ["library", "metadata", key, "thumb" | "art" | "composite", ..] => self.catalog.get(key).cloned(),
            _ => None,
        }?;
        is_photo_row(&row).then_some(row)
    }

    /// A picture's bytes for its own thumb, transcode or an album's cover: the file itself, read
    /// from its source as its Part is. PMS answers a resized JPEG; rex has no resizer (CLAUDE.md,
    /// the crate rule), so a client draws the picture at its own size.
    /// ponytail: full-size bytes for every tile; resize with REX_TRANSCODER if a client chokes.
    fn photo_bytes(&mut self, row: &Value, request: &Request) -> Response {
        let picture = if is_album(row) { row.get("ratingKey").map(string_value).and_then(|key| self.album_cover(&key)) } else { Some(row.clone()) };
        let Some(part) = picture.as_ref().and_then(|row| row.pointer("/Media/0/Part/0")) else {
            return Response::error(404, "rex: not found");
        };
        let key = part.get("key").map(string_value).unwrap_or_default();
        let segments: Vec<String> = key.trim_start_matches("/library/parts/").split('/').map(str::to_string).collect();
        let segments: Vec<&str> = segments.iter().map(String::as_str).collect();
        let mut response = self.route_part(&segments, &HashMap::new(), request);
        // PMS names every thumb, composite and transcode `image/jpeg`, a PNG's transcode
        // included, whose bytes it leaves a PNG (plexscratch, `E02P02.png` at 240 pixels).
        if response.status < 300 {
            response.content_type = "image/jpeg".to_string();
        }
        response
    }

    fn photo_section_fields(&self, section: &str) -> (Value, Value, Value) {
        let found = self.section(section);
        let field = |name: &str| found.and_then(|row| row.get(name)).cloned().unwrap_or(Value::Null);
        (section.parse::<i64>().map(Value::from).unwrap_or_else(|_| json!(section)), field("title"), field("uuid"))
    }

    /// A detail or related container: the row's library, as plexscratch's.
    fn photo_container(&self, row: &Value, metadata: Vec<Value>) -> Map<String, Value> {
        let (id, title, uuid) = self.photo_section_fields(&string_value(row.get("librarySectionID").unwrap_or(&Value::Null)));
        let mut out = Map::new();
        out.insert("size".to_string(), json!(metadata.len()));
        out.insert("allowSync".to_string(), json!(true));
        // An empty shelf names the library plug-in; a detail does not.
        if metadata.is_empty() {
            out.insert("identifier".to_string(), json!("com.plexapp.plugins.library"));
        }
        out.insert("librarySectionID".to_string(), id);
        out.insert("librarySectionTitle".to_string(), title);
        out.insert("librarySectionUUID".to_string(), uuid);
        if !metadata.is_empty() {
            out.insert("Metadata".to_string(), Value::Array(metadata));
        }
        out
    }

    /// A row as a listing or an album's children show it (plexscratch's `/all` and `/children`).
    fn photo_listed(&self, row: &Value) -> Value {
        let mut out = Map::new();
        let names: &[&str] = if is_album(row) {
            &["ratingKey", "key", "parentRatingKey", "guid", "type", "title", "parentKey", "summary", "index", "composite", "thumb", "art", "addedAt", "updatedAt"]
        } else {
            &["ratingKey", "key", "parentRatingKey", "guid", "type", "title", "parentKey", "summary", "index", "year", "thumb", "originallyAvailableAt",
              "addedAt", "updatedAt", "createdAtAccuracy", "createdAtTZOffset", "Media"]
        };
        copy(row, &mut out, names);
        out.insert("Image".to_string(), images(row, None));
        Value::Object(out)
    }

    /// A picture or album with its parents named: the detail and the play queue's rows
    /// (plexscratch's `/library/metadata/76` and `/playQueues/8`).
    fn photo_full(&self, row: &Value) -> Map<String, Value> {
        let parent = row.get("parentRatingKey").map(string_value).and_then(|key| self.catalog.get(&key).cloned());
        let grandparent = parent.as_ref().and_then(|row| row.get("parentRatingKey")).map(string_value).and_then(|key| self.catalog.get(&key).cloned());
        let of = |row: &Option<Value>, field: &str| row.as_ref().and_then(|row| row.get(field)).cloned();
        let mut out = Map::new();
        let mut put = |name: &str, value: Option<Value>| {
            if let Some(value) = value {
                out.insert(name.to_string(), value);
            }
        };
        let field = |name: &str| row.get(name).cloned();
        put("ratingKey", field("ratingKey"));
        put("key", field("key"));
        put("parentRatingKey", field("parentRatingKey"));
        put("grandparentRatingKey", of(&grandparent, "ratingKey"));
        put("guid", field("guid"));
        put("parentGuid", of(&parent, "guid"));
        put("grandparentGuid", of(&grandparent, "guid"));
        put("type", field("type"));
        put("title", field("title"));
        put("grandparentKey", grandparent.as_ref().map(|row| json!(format!("/library/metadata/{}", string_value(&row["ratingKey"])))));
        put("parentKey", field("parentKey"));
        put("librarySectionTitle", field("librarySectionTitle"));
        put("librarySectionID", field("librarySectionID"));
        put("librarySectionKey", field("librarySectionKey"));
        put("grandparentTitle", of(&grandparent, "title"));
        put("parentTitle", of(&parent, "title"));
        put("summary", field("summary"));
        put("index", field("index"));
        if is_album(row) {
            put("composite", field("composite"));
            put("parentIndex", parent.as_ref().map(|_| json!(1)));
            put("thumb", field("thumb"));
            put("art", field("art"));
            put("parentThumb", of(&parent, "thumb"));
            put("addedAt", field("addedAt"));
            put("updatedAt", field("updatedAt"));
            out.insert("Image".to_string(), images(row, None));
            return out;
        }
        put("parentIndex", parent.as_ref().map(|_| json!(1)));
        put("year", field("year"));
        put("thumb", field("thumb"));
        // A picture's background is its album's.
        put("art", of(&parent, "art"));
        put("parentThumb", of(&parent, "thumb"));
        put("grandparentThumb", of(&grandparent, "thumb"));
        put("grandparentArt", of(&grandparent, "art"));
        for name in ["originallyAvailableAt", "addedAt", "updatedAt", "createdAtAccuracy", "createdAtTZOffset", "Media"] {
            put(name, field(name));
        }
        out.insert("Image".to_string(), images(row, of(&parent, "art")));
        out
    }

    /// The pictures below `key` (a section's top or an album), at any depth, in title order: what
    /// a slideshow of it plays (plexscratch's `/playQueues` of the library listed its eight
    /// pictures from every album, `cover` to `sketch`).
    fn pictures_below(&self, rows: Vec<Value>) -> Vec<Value> {
        let mut out = Vec::new();
        let mut stack = rows;
        while let Some(row) = stack.pop() {
            if is_album(&row) {
                stack.extend(self.catalog.children_refs(&string_value(&row["ratingKey"])).into_iter().cloned());
            } else {
                out.push(row);
            }
        }
        out.sort_by_key(|row| natural_key(row.get("title").and_then(Value::as_str).unwrap_or_default()));
        out
    }

    fn section_top(&self, section: &str) -> Vec<Value> {
        let mut rows: Vec<Value> = self.catalog.section_refs(section).into_iter().filter(|row| is_photo_row(row)).cloned().collect();
        rows.sort_by_key(|row| natural_key(row.get("title").and_then(Value::as_str).unwrap_or_default()));
        rows
    }

    /// `GET /library/sections/{id}/all`: the albums and pictures at the library's top in title
    /// order (`Name`, the active sort), or, under the Timeline's `clusterZoomLevel`, every picture
    /// newest first.
    fn photo_listing(&self, section: &str, query: &HashMap<String, String>) -> Response {
        let (id, title, uuid) = self.photo_section_fields(section);
        let rows = if query.contains_key("clusterZoomLevel") || query.contains_key("cluster") {
            let mut rows = self.pictures_below(self.section_top(section));
            rows.sort_by_key(|row| std::cmp::Reverse(row.get("addedAt").and_then(Value::as_i64).unwrap_or(0)));
            rows
        } else {
            self.section_top(section)
        };
        let mut out = Map::new();
        let (page, total, offset) = window(rows, query);
        out.insert("size".to_string(), json!(page.len()));
        if let Some(offset) = offset {
            out.insert("totalSize".to_string(), json!(total));
            out.insert("offset".to_string(), json!(offset));
        }
        out.insert("allowSync".to_string(), json!(true));
        out.insert("art".to_string(), json!("/:/resources/photo-fanart.jpg"));
        out.insert("identifier".to_string(), json!("com.plexapp.plugins.library"));
        out.insert("librarySectionID".to_string(), id);
        out.insert("librarySectionTitle".to_string(), title.clone());
        out.insert("librarySectionUUID".to_string(), uuid);
        out.insert("mediaTagPrefix".to_string(), json!(MEDIA_TAG_PREFIX));
        out.insert("mediaTagVersion".to_string(), json!(MEDIA_TAG_VERSION));
        out.insert("nocache".to_string(), json!(true));
        out.insert("thumb".to_string(), json!("/:/resources/photo.png"));
        out.insert("title1".to_string(), title);
        out.insert("title2".to_string(), json!("All Photos"));
        out.insert("viewGroup".to_string(), json!("photo"));
        out.insert("viewMode".to_string(), json!(131633));
        if query.get("includeMeta").map(String::as_str) == Some("1") {
            if let Some(meta) = self.photo_meta(section) {
                out.insert("Meta".to_string(), meta);
            }
        }
        out.insert("Metadata".to_string(), Value::Array(page.iter().map(|row| self.photo_listed(row)).collect()));
        Response::json(200, json!({"MediaContainer": out}))
    }

    /// The photo library's `Meta`, measured whole (`data/section_meta_photo.json`).
    fn photo_meta(&self, section: &str) -> Option<Value> {
        let template = load_json_sidecar(&self.catalog_path, "section_meta_photo.json")?.get("plain")?.clone();
        let blob = serde_json::to_string(&template).ok()?.replace("/library/sections/10/", &format!("/library/sections/{section}/"));
        let mut meta: Value = serde_json::from_str(&blob).ok()?;
        if let Some(first) = meta.pointer_mut("/Type/0/key") {
            *first = json!(format!("/library/sections/{section}/all"));
        }
        Some(meta)
    }

    /// `GET /library/sections/{id}/cluster`: the Timeline's moments, one a day, as plexscratch
    /// answered its eight pictures of one day: `startsAt`/`endsAt` the day's first and last
    /// picture, `avgAR` their mean width over height.
    fn photo_clusters(&self, section: &str) -> Response {
        let (_, title, _) = self.photo_section_fields(section);
        let mut pictures = self.pictures_below(self.section_top(section));
        pictures.sort_by_key(|row| row.get("addedAt").and_then(Value::as_i64).unwrap_or(0));
        let mut days: Vec<(String, Vec<Value>)> = Vec::new();
        for row in pictures {
            let day = row.get("originallyAvailableAt").map(string_value).unwrap_or_default();
            match days.iter_mut().find(|(d, _)| *d == day) {
                Some((_, rows)) => rows.push(row),
                None => days.push((day, vec![row])),
            }
        }
        days.reverse();
        let directories: Vec<Value> = days
            .iter()
            .enumerate()
            .map(|(index, (day, rows))| {
                // PMS's moment is the picture's local time read as UTC.
                let at = |row: &Value| row.get("addedAt").and_then(Value::as_i64).unwrap_or(0)
                    - row.get("createdAtTZOffset").map(string_value).and_then(|offset| offset.parse::<i64>().ok()).unwrap_or(0);
                let ratios: Vec<f64> = rows
                    .iter()
                    .filter_map(|row| Some(row.pointer("/Media/0/width")?.as_f64()? / row.pointer("/Media/0/height")?.as_f64()?))
                    .collect();
                let average = if ratios.is_empty() { 0.0 } else { ratios.iter().sum::<f64>() / ratios.len() as f64 };
                json!({
                    "fastKey": format!("/library/sections/{section}/all?cluster={}&clustering<=8", index + 1),
                    "title": day_title(day), "id": index + 1, "size": rows.len(),
                    "startsAt": rows.iter().map(at).min().unwrap_or(0), "endsAt": rows.iter().map(at).max().unwrap_or(0),
                    "avgAR": format!("{average:.2}"),
                })
            })
            .collect();
        Response::json(200, json!({"MediaContainer": {
            "size": directories.len(), "allowSync": false, "art": "/:/resources/photo-fanart.jpg", "clusterZoomLevel": 1,
            "clusteringActive": false, "content": "secondary", "identifier": "com.plexapp.plugins.library",
            "key": format!("/library/sections/{section}/all?clusterZoomLevel=1&clustering<=8"),
            "mediaTagPrefix": MEDIA_TAG_PREFIX, "mediaTagVersion": MEDIA_TAG_VERSION, "nocache": true,
            "thumb": "/:/resources/photo.png", "title1": title, "title2": "By Moment", "viewGroup": "secondary",
            "viewMode": 65592, "Directory": directories,
        }}))
    }

    /// `GET /hubs/sections/{id}`: Recently Added in Photos (newest first, then by key), and the
    /// two shelves a library with no favourites and no photo playlists answers empty.
    fn photo_hubs(&self, section: &str, query: &HashMap<String, String>) -> Response {
        let (id, title, uuid) = self.photo_section_fields(section);
        let count = query.get("count").and_then(|count| count.parse::<usize>().ok()).unwrap_or(12);
        let mut recent = self.pictures_below(self.section_top(section));
        recent.sort_by_key(|row| {
            (std::cmp::Reverse(row.get("addedAt").and_then(Value::as_i64).unwrap_or(0)), row.get("ratingKey").map(string_value).and_then(|key| key.parse::<i64>().ok()).unwrap_or(0))
        });
        recent.truncate(count);
        let keys: Vec<String> = recent.iter().map(|row| string_value(&row["ratingKey"])).collect();
        let rows: Vec<Value> = recent
            .iter()
            .map(|row| {
                let mut out = Map::new();
                copy(row, &mut out, &["ratingKey", "key", "parentRatingKey", "guid", "type", "title", "parentKey", "librarySectionTitle", "librarySectionID",
                                      "librarySectionKey", "index", "year", "thumb", "originallyAvailableAt", "addedAt", "updatedAt", "createdAtAccuracy", "createdAtTZOffset"]);
                out.insert("Image".to_string(), images(row, None));
                Value::Object(out)
            })
            .collect();
        let empty = |key: String, title: String, kind: &str, identifier: String, context: &str| {
            json!({"key": key, "title": title, "type": kind, "hubIdentifier": identifier, "context": context, "size": 0, "more": false})
        };
        let mut hubs = Vec::new();
        if !rows.is_empty() {
            hubs.push(json!({"hubKey": format!("/library/metadata/{}", keys.join(",")),
                             "key": format!("/hubs/home/recentlyAdded?type=13&sectionID={section}"),
                             "title": format!("Recently Added in {}", string_value(&title)), "type": "photo",
                             "hubIdentifier": format!("photo.recent.{section}"), "context": "hub.photo.recent",
                             "size": rows.len(), "more": false, "Metadata": rows}));
        }
        hubs.push(empty(format!("/playlists/all?type=15&playlistType=photo&sort=lastViewedAt:desc&sectionID={section}"), "Recent Albums".to_string(),
                        "playlist", format!("recent.library.playlists.{section}"), "hub.recent.library.playlists"));
        hubs.push(empty(format!("/library/sections/{section}/all?type=13,12&userRating=10&sort=updatedAt:desc"), "Recently Favorited Photos".to_string(),
                        "photo", format!("photo.recent.favorites.{section}"), "hub.photo.recent.favorites"));
        Response::json(200, json!({"MediaContainer": {"size": hubs.len(), "allowSync": true, "identifier": "com.plexapp.plugins.library",
                                                      "librarySectionID": id, "librarySectionTitle": title, "librarySectionUUID": uuid, "Hub": hubs}}))
    }

    /// `GET /library/metadata/{album}/children`: its albums and pictures in title order, in a
    /// container naming the album and the album above it (plexscratch's `75/children`).
    fn album_children(&self, album: &Value, query: &HashMap<String, String>) -> Response {
        let key = string_value(&album["ratingKey"]);
        let mut rows: Vec<Value> = self.catalog.children_refs(&key).into_iter().cloned().collect();
        rows.sort_by_key(|row| natural_key(row.get("title").and_then(Value::as_str).unwrap_or_default()));
        let parent = album.get("parentRatingKey").map(string_value).and_then(|key| self.catalog.get(&key).cloned());
        let (id, section_title, uuid) = self.photo_section_fields(&string_value(album.get("librarySectionID").unwrap_or(&Value::Null)));
        let (page, total, offset) = window(rows, query);
        let mut out = Map::new();
        out.insert("size".to_string(), json!(page.len()));
        if let Some(offset) = offset {
            out.insert("totalSize".to_string(), json!(total));
            out.insert("offset".to_string(), json!(offset));
        }
        out.insert("allowSync".to_string(), json!(true));
        out.insert("art".to_string(), album.get("art").cloned().unwrap_or(Value::Null));
        if let Some(parent) = &parent {
            out.insert("grandparentRatingKey".to_string(), parent["ratingKey"].as_str().and_then(|key| key.parse::<i64>().ok()).map_or(parent["ratingKey"].clone(), Value::from));
            out.insert("grandparentThumb".to_string(), parent.get("thumb").cloned().unwrap_or(Value::Null));
            out.insert("grandparentTitle".to_string(), parent.get("title").cloned().unwrap_or(Value::Null));
        }
        out.insert("identifier".to_string(), json!("com.plexapp.plugins.library"));
        out.insert("key".to_string(), json!(key));
        out.insert("librarySectionID".to_string(), id);
        out.insert("librarySectionTitle".to_string(), section_title.clone());
        out.insert("librarySectionUUID".to_string(), uuid);
        out.insert("mediaTagPrefix".to_string(), json!(MEDIA_TAG_PREFIX));
        out.insert("mediaTagVersion".to_string(), json!(MEDIA_TAG_VERSION));
        out.insert("nocache".to_string(), json!(true));
        out.insert("parentIndex".to_string(), json!(1));
        out.insert("parentTitle".to_string(), album.get("title").cloned().unwrap_or(Value::Null));
        out.insert("thumb".to_string(), album.get("thumb").cloned().unwrap_or(Value::Null));
        out.insert("title1".to_string(), parent.as_ref().and_then(|parent| parent.get("title").cloned()).unwrap_or(section_title));
        out.insert("title2".to_string(), album.get("title").cloned().unwrap_or(Value::Null));
        out.insert("viewGroup".to_string(), json!("photo"));
        out.insert("viewMode".to_string(), json!(131633));
        out.insert("Metadata".to_string(), Value::Array(page.iter().map(|row| self.photo_listed(row)).collect()));
        Response::json(200, json!({"MediaContainer": out}))
    }

    /// `POST /playQueues?type=photo`: a slideshow of the album or library `uri` names, its
    /// pictures in title order, starting on `key` (plexscratch: from a picture,
    /// `uri=.../library/metadata/75/children&key=/library/metadata/76`; the album's Play,
    /// `uri=.../library/metadata/75`; a picture at the library's top, the library's listing).
    fn photo_queue(&mut self, query: &HashMap<String, String>) -> Option<Response> {
        let uri = query.get("uri")?;
        let path = uri.split_once("/com.plexapp.plugins.library").map(|(_, path)| path)?.to_string();
        let bare = path.split('?').next().unwrap_or_default();
        let segments: Vec<&str> = bare.trim_start_matches('/').split('/').collect();
        let (rows, directory) = match segments.as_slice() {
            ["library", "sections", section, "all"] if self.photo_section(section) => (self.section_top(section), true),
            ["library", "metadata", key, "children"] => (self.catalog.children_refs(key).into_iter().cloned().collect(), true),
            ["library", "metadata", key] => (self.catalog.get(key).cloned().into_iter().collect(), false),
            _ => return None,
        };
        if !rows.iter().all(is_photo_row) || rows.is_empty() {
            return None;
        }
        let pictures = self.pictures_below(rows);
        let wanted = query.get("key").and_then(|key| key.rsplit('/').next()).map(str::to_string);
        let id = self.next_queue.to_string();
        self.next_queue += 1;
        let items: Vec<QueueItem> = pictures
            .iter()
            .map(|row| {
                let item = QueueItem { id: self.next_queue_item, rating_key: string_value(&row["ratingKey"]), added: false, media: None };
                self.next_queue_item += 1;
                item
            })
            .collect();
        let selected = items.iter().find(|item| Some(&item.rating_key) == wanted.as_ref()).or(items.first()).map(|item| item.id);
        let source = format!("library://x/{}/{}", if directory { "directory" } else { "item" }, quote_all(&path));
        let natural = items.iter().map(|item| item.rating_key.clone()).collect();
        self.queues.insert(id.clone(), Queue {
            id: id.clone(), version: 1, selected, items, source, natural, shuffled: false, last_added: None, client: String::new(),
            playlist_id: None, playlist_title: None, main_key: None, deselected: false,
        });
        self.dirty = true;
        Some(self.photo_queue_answer(&id))
    }

    fn photo_queue_answer(&self, id: &str) -> Response {
        let Some(queue) = self.queues.get(id) else {
            return Response::error(404, "rex: not found");
        };
        let offset = queue.items.iter().position(|item| Some(item.id) == queue.selected).unwrap_or(0);
        let rows: Vec<Value> = queue
            .items
            .iter()
            .filter_map(|item| {
                let row = self.catalog.get(&item.rating_key)?;
                let mut out = Map::from_iter([("playQueueItemID".to_string(), json!(item.id))]);
                out.extend(self.photo_full(row));
                Some(Value::Object(out))
            })
            .collect();
        let selected = queue.selected.and_then(|selected| queue.items.iter().find(|item| item.id == selected));
        Response::json(200, json!({"MediaContainer": {
            "size": rows.len(), "identifier": "com.plexapp.plugins.library", "mediaTagPrefix": MEDIA_TAG_PREFIX,
            "mediaTagVersion": MEDIA_TAG_VERSION, "playQueueID": id.parse::<i64>().unwrap_or(0),
            "playQueueSelectedItemID": queue.selected, "playQueueSelectedItemOffset": offset,
            "playQueueSelectedMetadataItemID": selected.map(|item| item.rating_key.clone()), "playQueueShuffled": queue.shuffled,
            "playQueueSourceURI": queue.source, "playQueueTotalCount": rows.len(), "playQueueVersion": queue.version,
            "Metadata": rows,
        }}))
    }
}

pub(super) fn is_photo_row(row: &Value) -> bool {
    row.get("type").and_then(Value::as_str) == Some("photo") && row.get("_element").is_some()
}

fn is_album(row: &Value) -> bool {
    row.get("_element").and_then(Value::as_str) == Some("Directory")
}

fn copy(row: &Value, out: &mut Map<String, Value>, names: &[&str]) {
    for name in names {
        if let Some(value) = row.get(*name) {
            out.insert(name.to_string(), value.clone());
        }
    }
}

/// `Image`: a picture's own cover (and its album's background where a row names it), an album's
/// cover and background, as plexscratch lists them.
fn images(row: &Value, background: Option<Value>) -> Value {
    let alt = row.get("title").cloned().unwrap_or(Value::Null);
    let mut out = vec![json!({"alt": alt, "type": "coverPoster", "url": row.get("thumb").cloned().unwrap_or(Value::Null)})];
    if let Some(art) = if is_album(row) { row.get("art").cloned() } else { background } {
        out.push(json!({"alt": alt, "type": "background", "url": art}));
    }
    Value::Array(out)
}

/// The page a client asked for (`X-Plex-Container-Start`/`-Size`), the whole count, and the
/// offset when it asked for one: an unpaged listing names neither `totalSize` nor `offset`.
fn window(rows: Vec<Value>, query: &HashMap<String, String>) -> (Vec<Value>, usize, Option<usize>) {
    let total = rows.len();
    let start = query.get("X-Plex-Container-Start").and_then(|v| v.parse::<usize>().ok());
    let size = query.get("X-Plex-Container-Size").and_then(|v| v.parse::<usize>().ok());
    if start.is_none() && size.is_none() {
        return (rows, total, None);
    }
    let start = start.unwrap_or(0).min(total);
    let end = size.map_or(total, |size| (start + size).min(total));
    (rows[start..end].to_vec(), total, Some(start))
}

/// `Nov 4, 2026`, a moment's title.
fn day_title(day: &str) -> String {
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let parts: Vec<usize> = day.split('-').filter_map(|part| part.parse().ok()).collect();
    match parts.as_slice() {
        [year, month, day] if (1..=12).contains(month) => format!("{} {day}, {year}", MONTHS[month - 1]),
        _ => day.to_string(),
    }
}

/// A queue's source path as PMS writes it in `playQueueSourceURI`: every reserved byte escaped.
fn quote_all(path: &str) -> String {
    path.bytes()
        .map(|byte| if byte.is_ascii_alphanumeric() || b"-._~".contains(&byte) { (byte as char).to_string() } else { format!("%{byte:02X}") })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn natural_order_is_plexscratchs() {
        let mut titles = vec!["page 10", "page 2", "page 1", "Sketches", "E02P03", "E02P01"];
        titles.sort_by_key(|title| natural_key(title));
        assert_eq!(titles, ["E02P01", "E02P03", "page 1", "page 2", "page 10", "Sketches"]);
    }

    #[test]
    fn images_only() {
        assert!(is_image("comics/a/b.cbz/001.JPG"));
        assert!(is_image("x/y.webp"));
        assert!(!is_image("comics/a/b.cbz"));
        assert!(!is_image("movies/a.mkv"));
    }
}
