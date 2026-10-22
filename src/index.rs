//! The persisted snapshot: scanning, postings, validation, and source freshness.
use crate::source::{Chunk, Source, digest, supported};
use crate::terms::Analyzer;
use anyhow::{Context, Result, ensure};
use ignore::WalkBuilder;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufWriter, Write};
use std::path::{Component, Path, PathBuf};

/// Schema 2: compact binary storage without excerpt text (read from the checked source).
const SCHEMA: u32 = 2;
/// Schema 1 exported by [`Index::export_legacy`] for tooling that reads the old JSON layout.
const LEGACY_SCHEMA: u32 = 1;
const MAX_PATH_WEIGHT: u32 = 10;
const MAX_FILE_BYTES: u64 = 1_000_000;
const SKIPPED_DIRS: [&str; 5] = ["node_modules", "vendor", "target", "dist", "__pycache__"];

#[derive(Debug, Serialize, Deserialize)]
pub struct Posting {
    /// Ordinal of the chunk in [`Index::chunks`] order.
    pub chunk: u32,
    pub count: u32,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Index {
    pub schema: u32,
    pub root: PathBuf,
    pub files: BTreeMap<String, Source>,
    pub postings: BTreeMap<String, Vec<Posting>>,
    /// Term counts per chunk, in [`Index::chunks`] order.
    pub lengths: Vec<u32>,
    /// Always stored (bincode is not self-describing); [`Index::export_legacy`] omits the
    /// accepted analyzer as schema 1 did.
    pub analyzer: Analyzer,
}

#[derive(Debug, Serialize)]
pub struct IndexStats {
    pub files: usize,
    pub chunks: usize,
    pub terms: usize,
    pub source_bytes: usize,
    pub index_bytes: u64,
    pub kinds: BTreeMap<String, usize>,
}

#[derive(Debug, Serialize)]
pub struct BuildReport {
    pub changed_files: usize,
    pub reused_files: usize,
    pub deleted_files: usize,
    pub skipped_files: usize,
    pub stats: IndexStats,
}

/// Files selected by one scan, with how each was obtained.
#[derive(Default)]
struct Scan {
    files: BTreeMap<String, Source>,
    changed: usize,
    reused: usize,
    skipped: usize,
}

pub fn index_path(root: &Path) -> PathBuf {
    root.join(".graf/index.bin")
}

/// Where schema 1 lived; removed when a schema 2 index is written.
fn legacy_index_path(root: &Path) -> PathBuf {
    root.join(".graf/index.json")
}

impl Index {
    /// Every chunk in path order, then line order. Posting and length ordinals
    /// refer to positions in this sequence; it is the only place that order is defined.
    pub(crate) fn chunks(&self) -> impl Iterator<Item = &Chunk> {
        self.files.values().flat_map(|source| &source.chunks)
    }

    pub fn load(root: &Path) -> Result<Self> {
        let index: Self = bincode::deserialize(
            &fs::read(index_path(root)).context("No readable index; run `graf index` first")?,
        )
        .context("Invalid index; rebuild with `graf index --rebuild`")?;
        index.validate(root)?;
        Ok(index)
    }

    fn validate(&self, root: &Path) -> Result<()> {
        ensure!(
            self.schema == SCHEMA,
            "Unsupported index schema; run `graf index --rebuild`"
        );
        ensure!(
            self.root == root,
            "Index belongs to another root; run `graf index --rebuild`"
        );
        let chunk_count = self.chunks().count();
        ensure!(
            (1..=MAX_PATH_WEIGHT).contains(&self.analyzer.path_weight),
            "Invalid analyzer; run `graf index --rebuild`"
        );
        ensure!(
            self.lengths.len() == chunk_count,
            "Invalid chunk counts; run `graf index --rebuild`"
        );
        ensure!(
            self.postings
                .values()
                .flatten()
                .all(|p| (p.chunk as usize) < chunk_count && p.count > 0),
            "Invalid postings; run `graf index --rebuild`"
        );
        ensure!(
            self.files.iter().all(
                |(path, source)| source.chunks.iter().all(|c| c.path == *path
                    && c.start > 0
                    && c.start <= c.end
                    && c.end <= source.lines)
            ),
            "Invalid source ranges; run `graf index --rebuild`"
        );
        Ok(())
    }

    pub fn build(root: &Path, rebuild: bool) -> Result<BuildReport> {
        Self::build_with(root, rebuild, Analyzer::default())
    }

    /// Build with an experimental analyzer. Postings are always rebuilt, so
    /// switching analyzers reuses unchanged chunks safely.
    pub fn build_with(root: &Path, rebuild: bool, analyzer: Analyzer) -> Result<BuildReport> {
        ensure!(
            (1..=MAX_PATH_WEIGHT).contains(&analyzer.path_weight),
            "path weight must be between 1 and {MAX_PATH_WEIGHT}"
        );
        let previous = if !rebuild && index_path(root).exists() {
            Some(Self::load(root)?)
        } else {
            None
        };
        let scan = scan(root, previous.as_ref())?;
        let deleted = previous.as_ref().map_or(0, |p| {
            p.files
                .keys()
                .filter(|p| !scan.files.contains_key(*p))
                .count()
        });
        let index = Self::with_postings(root, scan.files, analyzer);
        index.save()?;
        Ok(BuildReport {
            changed_files: scan.changed,
            reused_files: scan.reused,
            deleted_files: deleted,
            skipped_files: scan.skipped,
            stats: index.stats()?,
        })
    }

    fn with_postings(root: &Path, files: BTreeMap<String, Source>, analyzer: Analyzer) -> Self {
        let mut index = Self {
            schema: SCHEMA,
            root: root.into(),
            files,
            postings: BTreeMap::new(),
            lengths: Vec::new(),
            analyzer,
        };
        let mut postings = BTreeMap::<String, Vec<Posting>>::new();
        let mut lengths = Vec::new();
        for chunk in index.chunks() {
            // Paths contribute terms along with source text.
            let mut counts = BTreeMap::new();
            for term in analyzer.window_terms(&chunk.path, &chunk.text) {
                *counts.entry(term).or_insert(0u32) += 1;
            }
            let ordinal = u32::try_from(lengths.len()).expect("fewer than 2^32 chunks");
            lengths.push(counts.values().sum());
            for (term, count) in counts {
                postings.entry(term).or_default().push(Posting {
                    chunk: ordinal,
                    count,
                });
            }
        }
        index.postings = postings;
        index.lengths = lengths;
        index
    }

    /// Replace the snapshot atomically; a failure leaves the previous one in place.
    fn save(&self) -> Result<()> {
        let dir = self.root.join(".graf");
        fs::create_dir_all(&dir)?;
        ensure!(
            !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
            ".graf must not be a symlink"
        );
        let mut temp = tempfile::NamedTempFile::new_in(&dir)?;
        {
            let mut output = BufWriter::new(temp.as_file_mut());
            bincode::serialize_into(&mut output, self)?;
            output.flush()?;
        }
        temp.as_file().sync_all()?;
        temp.persist(index_path(&self.root))
            .map_err(|e| e.error)
            .context("Cannot save index atomically")?;
        let legacy = legacy_index_path(&self.root);
        if legacy.is_file() {
            fs::remove_file(legacy)?; // graf's own schema 1 snapshot, superseded.
        }
        Ok(())
    }

    pub fn stats(&self) -> Result<IndexStats> {
        let mut kinds = BTreeMap::new();
        for file in self.files.values() {
            *kinds.entry(file.kind.clone()).or_insert(0) += 1;
        }
        Ok(IndexStats {
            files: self.files.len(),
            chunks: self.lengths.len(),
            terms: self.postings.len(),
            source_bytes: self.files.values().map(|s| s.bytes).sum(),
            index_bytes: fs::metadata(index_path(&self.root))?.len(),
            kinds,
        })
    }

    /// Read an indexed file and prove it still matches the snapshot.
    pub fn current_text(&self, path: &str) -> Result<String> {
        ensure!(
            Path::new(path)
                .components()
                .all(|c| matches!(c, Component::Normal(_))),
            "Unsafe source path"
        );
        let source = self.files.get(path).context("Source not indexed")?;
        let full = self.root.join(path);
        let canonical = full
            .canonicalize()
            .context("Source is missing; run `graf index`")?;
        ensure!(
            canonical.starts_with(&self.root),
            "Source escapes repository; run `graf index`"
        );
        // Disallow every symlink component, even when its destination remains inside the repo.
        let mut cursor = self.root.clone();
        for component in Path::new(path).components() {
            cursor.push(component);
            ensure!(
                !fs::symlink_metadata(&cursor)?.file_type().is_symlink(),
                "Source became a symlink; run `graf index`"
            );
        }
        let text =
            fs::read_to_string(full).context("Cannot read indexed source; run `graf index`")?;
        ensure!(
            digest(&text) == source.hash,
            "Stale source {path}; run `graf index`"
        );
        // The hash proves the content; this guards against an index whose ranges are wrong.
        let lines = text.lines().count();
        ensure!(
            source.chunks.iter().all(|chunk| chunk.end <= lines),
            "Corrupt source excerpts for {path}; run `graf index --rebuild`"
        );
        Ok(text)
    }

    /// An excerpt's text, from the snapshot-checked source text of its file.
    pub(crate) fn excerpt_text(text: &str, chunk: &Chunk) -> String {
        let lines: Vec<&str> = text.lines().collect();
        lines[chunk.start - 1..chunk.end].join("\n")
    }

    /// The index in the schema 1 JSON layout, excerpt text included, for tooling and
    /// equivalence checks against earlier builds.
    pub fn export_legacy(&self) -> Result<serde_json::Value> {
        let mut files = serde_json::Map::new();
        for (path, source) in &self.files {
            let text = self.current_text(path)?;
            let chunks: Vec<_> = source
                .chunks
                .iter()
                .map(|c| {
                    serde_json::json!({"id": c.id, "path": c.path, "start": c.start, "end": c.end,
                                       "text": Self::excerpt_text(&text, c)})
                })
                .collect();
            files.insert(
                path.clone(),
                serde_json::json!({"hash": source.hash, "bytes": source.bytes, "lines": source.lines,
                                   "kind": source.kind, "chunks": chunks}),
            );
        }
        let mut value = serde_json::json!({
            "schema": LEGACY_SCHEMA, "root": self.root, "files": files,
            "postings": self.postings, "lengths": self.lengths,
        });
        if !self.analyzer.is_default() {
            value["analyzer"] = serde_json::to_value(self.analyzer)?;
        }
        Ok(value)
    }
}

/// Walk the root once, reusing previous sources whose content hash is unchanged.
fn scan(root: &Path, previous: Option<&Index>) -> Result<Scan> {
    let mut scan = Scan::default();
    // Respect repository/global ignore rules even in a directory without .git.
    let mut walk = WalkBuilder::new(root);
    walk.parents(false)
        .hidden(true)
        .follow_links(false)
        .require_git(false)
        .filter_entry(|entry| {
            entry.depth() == 0
                || !entry.file_type().is_some_and(|t| t.is_dir())
                || !entry
                    .file_name()
                    .to_str()
                    .is_some_and(|name| SKIPPED_DIRS.contains(&name))
        });
    for entry in walk.build() {
        let entry = entry.context("Cannot traverse repository")?;
        if !entry.file_type().is_some_and(|t| t.is_file()) || !supported(entry.path()) {
            continue;
        }
        let Some(text) = read_eligible(&entry)? else {
            scan.skipped += 1;
            continue;
        };
        let rel = entry
            .path()
            .strip_prefix(root)?
            .to_str()
            .context("Non-UTF-8 source path")?
            .replace(std::path::MAIN_SEPARATOR, "/");
        // Reject symlinked parents, including concurrent replacements, before reading later.
        ensure!(
            entry.path().canonicalize()?.starts_with(root),
            "Source escapes repository: {rel}"
        );
        let hash = digest(&text);
        match previous
            .and_then(|p| p.files.get(&rel))
            .filter(|s| s.hash == hash)
        {
            Some(_) => {
                // Excerpt text is not stored, so the unchanged source is re-split from the
                // text just read; splitting is deterministic, so ids and ranges are unchanged.
                scan.files
                    .insert(rel.clone(), Source::new(&rel, &text, hash));
                scan.reused += 1;
            }
            None => {
                let source = Source::new(&rel, &text, hash);
                scan.files.insert(rel, source);
                scan.changed += 1;
            }
        }
    }
    Ok(scan)
}

/// Text of a supported file, or `None` when it is oversized, binary, or not UTF-8.
fn read_eligible(entry: &ignore::DirEntry) -> Result<Option<String>> {
    if entry.metadata()?.len() > MAX_FILE_BYTES {
        return Ok(None);
    }
    match fs::read_to_string(entry.path()) {
        Ok(text) if !text.contains('\0') && text.len() <= MAX_FILE_BYTES as usize => Ok(Some(text)),
        Ok(_) => Ok(None),
        Err(e) if e.kind() == std::io::ErrorKind::InvalidData => Ok(None),
        Err(e) => Err(e).context("Cannot read source file"),
    }
}
