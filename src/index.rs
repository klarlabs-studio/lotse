//! The persisted snapshot: scanning, postings, validation, and source freshness.
use crate::source::{CHUNK_LINES, Chunk, Source, digest, kind, supported};
use crate::terms::Analyzer;
use anyhow::{Context, Result, bail, ensure};
use bincode::Options;
use ignore::WalkBuilder;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{BufWriter, Read, Write};
use std::path::{Component, Path, PathBuf};

/// Schema 2: compact binary storage without excerpt text (read from the checked source).
const SCHEMA: u32 = 2;
/// Schema 1 exported by [`Index::export_legacy`] for tooling that reads the old JSON layout.
const LEGACY_SCHEMA: u32 = 1;
const MAX_PATH_WEIGHT: u32 = 10;
const MAX_FILE_BYTES: u64 = 1_000_000;
/// Keep damaged or unexpectedly large snapshots from consuming unbounded memory. Real indexes
/// stay well below: VS Code's was 159 MB and Kubernetes' 132 MB (research/scale-results.md).
pub const MAX_INDEX_BYTES: u64 = 1024 * 1024 * 1024;
const SKIPPED_DIRS: [&str; 5] = ["node_modules", "vendor", "target", "dist", "__pycache__"];

#[derive(Debug, Clone, Serialize, Deserialize)]
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
    /// Files whose source and postings can both be carried forward unchanged.
    reused_paths: BTreeSet<String>,
    changed: usize,
    reused: usize,
    skipped: usize,
}

/// Rename `temp` over `target` with `fs::rename`, which uses POSIX semantics on Windows 10+, so
/// readers never see the target missing. Windows can still refuse briefly while another writer's
/// rename is in flight; retry that a few times.
pub(crate) fn replace(temp: tempfile::TempPath, target: &Path) -> std::io::Result<()> {
    const ATTEMPTS: u32 = 20;
    let mut attempt = 1;
    loop {
        match fs::rename(&temp, target) {
            Ok(()) => {
                // The file now lives at `target`; nothing is left to clean up.
                let _ = temp.keep();
                return Ok(());
            }
            Err(error)
                if cfg!(windows)
                    && error.kind() == std::io::ErrorKind::PermissionDenied
                    && attempt < ATTEMPTS =>
            {
                std::thread::sleep(std::time::Duration::from_millis(5 * u64::from(attempt)));
                attempt += 1;
            }
            Err(error) => return Err(error),
        }
    }
}

pub fn index_path(root: &Path) -> PathBuf {
    root.join(".graf/index.bin")
}

/// Return `None` only when the snapshot is absent. Oversized snapshots are a
/// resource-limit error, not corruption: automatic rebuilding them on every
/// request would repeatedly rescan large repositories and discard valid data.
fn read_index_bytes(root: &Path) -> Result<Option<Vec<u8>>> {
    let path = index_path(root);
    let file = match fs::File::open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("Cannot open index"),
    };
    let size = file.metadata().context("Cannot inspect index")?.len();
    if size > MAX_INDEX_BYTES {
        bail!("Index exceeds the {MAX_INDEX_BYTES}-byte resource limit");
    }
    let mut bytes = Vec::with_capacity(size as usize);
    file.take(MAX_INDEX_BYTES + 1)
        .read_to_end(&mut bytes)
        .context("Cannot read index")?;
    if bytes.len() as u64 > MAX_INDEX_BYTES {
        bail!("Index exceeds the {MAX_INDEX_BYTES}-byte resource limit");
    }
    Ok(Some(bytes))
}

fn deserialize_index(bytes: &[u8]) -> Result<Index> {
    bincode::DefaultOptions::new()
        .with_fixint_encoding()
        .with_limit(MAX_INDEX_BYTES)
        .allow_trailing_bytes()
        .deserialize(bytes)
        .context("Invalid index serialization")
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
        let bytes = read_index_bytes(root)?.context("No readable index; run `graf index` first")?;
        let index = deserialize_index(&bytes)
            .context("Invalid index; rebuild with `graf index --rebuild`")?;
        index.validate(root)?;
        Ok(index)
    }

    /// Load an index for an MCP request, rebuilding only when its snapshot is
    /// absent or invalid. Filesystem and permission errors are returned to the
    /// caller. A readable analyzer is retained across recoverable data damage.
    /// Ordinary [`Index::load`] remains strict for CLI callers.
    pub fn load_or_rebuild_for_mcp(root: &Path) -> Result<Self> {
        let bytes = read_index_bytes(root)?;
        let parsed = bytes
            .as_deref()
            .and_then(|data| deserialize_index(data).ok());
        if let Some(index) = parsed.as_ref()
            && index.validate(root).is_ok()
        {
            return Ok(parsed.expect("parsed index exists"));
        }

        // The analyzer is independent of the derived postings. Keep it when it
        // decoded successfully and passed its own bounds, even if other index
        // data or the schema needs rebuilding.
        let analyzer = parsed
            .as_ref()
            .map(|index| index.analyzer)
            .filter(Self::valid_analyzer)
            .unwrap_or_default();
        Self::build_with(root, true, analyzer)?;
        Self::load(root)
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
            Self::valid_analyzer(&self.analyzer),
            "Invalid analyzer; run `graf index --rebuild`"
        );
        ensure!(
            self.lengths.len() == chunk_count,
            "Invalid chunk counts; run `graf index --rebuild`"
        );
        let mut calculated_lengths = vec![0u32; chunk_count];
        for (path, source) in &self.files {
            let components = Path::new(path).components();
            let normalized_path = components
                .map(|component| match component {
                    Component::Normal(part) => part.to_str(),
                    _ => None,
                })
                .collect::<Option<Vec<_>>>()
                .map(|parts| parts.join("/"));
            ensure!(
                !path.is_empty()
                    && !path.contains('\\')
                    && normalized_path.as_deref() == Some(path.as_str())
                    && supported(Path::new(path)),
                "Invalid source path; run `graf index --rebuild`"
            );
            ensure!(
                source.bytes <= MAX_FILE_BYTES as usize
                    && source.lines <= source.bytes
                    && source.hash.len() == 64
                    && source.hash.bytes().all(|b| b.is_ascii_hexdigit())
                    && source.kind == kind(path),
                "Invalid source metadata; run `graf index --rebuild`"
            );
            let mut previous_end = 0usize;
            for chunk in &source.chunks {
                ensure!(
                    chunk.path == *path
                        && chunk.start > previous_end
                        && chunk.start > 0
                        && chunk.start <= chunk.end
                        && chunk.end <= source.lines
                        && chunk.end - chunk.start < CHUNK_LINES
                        && (chunk.start - 1) % CHUNK_LINES == 0
                        && chunk.id.len() == 64
                        && chunk.id
                            == digest(&format!(
                                "{path}\0{}\0{}\0{}",
                                source.hash, chunk.start, chunk.end
                            )),
                    "Invalid source ranges; run `graf index --rebuild`"
                );
                previous_end = chunk.end;
            }
        }
        for (term, postings) in &self.postings {
            ensure!(
                !term.is_empty() && !postings.is_empty(),
                "Invalid postings; run `graf index --rebuild`"
            );
            let mut previous = None;
            for posting in postings {
                let ordinal = posting.chunk as usize;
                ensure!(
                    ordinal < chunk_count
                        && posting.count > 0
                        && previous.is_none_or(|p| p < ordinal),
                    "Invalid postings; run `graf index --rebuild`"
                );
                previous = Some(ordinal);
                calculated_lengths[ordinal] = calculated_lengths[ordinal]
                    .checked_add(posting.count)
                    .context("Invalid postings; run `graf index --rebuild`")?;
            }
        }
        ensure!(
            calculated_lengths == self.lengths,
            "Invalid chunk lengths; run `graf index --rebuild`"
        );
        Ok(())
    }

    fn valid_analyzer(analyzer: &Analyzer) -> bool {
        (1..=MAX_PATH_WEIGHT).contains(&analyzer.path_weight)
    }

    pub fn build(root: &Path, rebuild: bool) -> Result<BuildReport> {
        Self::build_with(root, rebuild, Analyzer::default())
    }

    /// Check the complete eligible source set, including new and removed files.
    /// Hash content rather than trusting mtimes; unchanged snapshots are not rewritten.
    pub(crate) fn refresh(&mut self) -> Result<bool> {
        let scan = scan(&self.root, Some(self), true)?;
        if scan.changed == 0 && scan.files.len() == self.files.len() {
            return Ok(false);
        }
        let refreshed = Self::with_postings(
            &self.root,
            scan.files,
            self.analyzer,
            Some(self),
            &scan.reused_paths,
        );
        refreshed.save()?;
        *self = refreshed;
        Ok(true)
    }

    /// Build with an experimental analyzer. Matching analyzers allow unchanged
    /// source postings to be remapped; analyzer switches retokenize all sources.
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
        let can_reuse_postings = previous
            .as_ref()
            .is_some_and(|previous| previous.analyzer == analyzer);
        let scan = scan(root, previous.as_ref(), can_reuse_postings)?;
        let deleted = previous.as_ref().map_or(0, |p| {
            p.files
                .keys()
                .filter(|p| !scan.files.contains_key(*p))
                .count()
        });
        let index = Self::with_postings(
            root,
            scan.files,
            analyzer,
            previous.as_ref().filter(|_| can_reuse_postings),
            &scan.reused_paths,
        );
        index.save()?;
        Ok(BuildReport {
            changed_files: scan.changed,
            reused_files: scan.reused,
            deleted_files: deleted,
            skipped_files: scan.skipped,
            stats: index.stats()?,
        })
    }

    fn with_postings(
        root: &Path,
        files: BTreeMap<String, Source>,
        analyzer: Analyzer,
        previous: Option<&Index>,
        reused_paths: &BTreeSet<String>,
    ) -> Self {
        let mut index = Self {
            schema: SCHEMA,
            root: root.into(),
            files,
            postings: BTreeMap::new(),
            lengths: Vec::new(),
            analyzer,
        };
        let chunk_count = index.chunks().count();
        let mut lengths = vec![0; chunk_count];
        let mut old_to_new = Vec::new();
        if let Some(previous) = previous {
            old_to_new.resize(previous.lengths.len(), None);
            let mut old_offsets = BTreeMap::new();
            let mut offset = 0usize;
            for (path, source) in &previous.files {
                old_offsets.insert(path.as_str(), offset);
                offset += source.chunks.len();
            }
            let mut new_ordinal = 0usize;
            for (path, source) in &index.files {
                if reused_paths.contains(path) {
                    let old_source = &previous.files[path];
                    let old_start = old_offsets[path.as_str()];
                    for local in 0..source.chunks.len() {
                        let old_ordinal = old_start + local;
                        old_to_new[old_ordinal] = Some(new_ordinal);
                        lengths[new_ordinal] = previous.lengths[old_ordinal];
                        new_ordinal += 1;
                    }
                    debug_assert_eq!(source.chunks.len(), old_source.chunks.len());
                } else {
                    new_ordinal += source.chunks.len();
                }
            }
        }

        // Remap retained posting ordinals in one pass over the prior posting map.
        // File order is stable for retained files, so these vectors remain sorted.
        let mut postings = BTreeMap::<String, Vec<Posting>>::new();
        if let Some(previous) = previous {
            for (term, old_list) in &previous.postings {
                let retained: Vec<_> = old_list
                    .iter()
                    .filter_map(|posting| {
                        old_to_new[posting.chunk as usize].map(|chunk| Posting {
                            chunk: u32::try_from(chunk).expect("fewer than 2^32 chunks"),
                            count: posting.count,
                        })
                    })
                    .collect();
                if !retained.is_empty() {
                    postings.insert(term.clone(), retained);
                }
            }
        }

        // Tokenize only files that were not carried forward. Additions are kept
        // separately, then merged with retained lists in ordinal order.
        let mut additions = BTreeMap::<String, Vec<Posting>>::new();
        let mut ordinal = 0usize;
        for (path, source) in &index.files {
            let reused = reused_paths.contains(path);
            for chunk in &source.chunks {
                if !reused {
                    let mut counts = BTreeMap::new();
                    for term in analyzer.window_terms(&chunk.path, &chunk.text) {
                        *counts.entry(term).or_insert(0u32) += 1;
                    }
                    lengths[ordinal] = counts.values().sum();
                    let chunk_ordinal = u32::try_from(ordinal).expect("fewer than 2^32 chunks");
                    for (term, count) in counts {
                        additions.entry(term).or_default().push(Posting {
                            chunk: chunk_ordinal,
                            count,
                        });
                    }
                }
                ordinal += 1;
            }
        }
        for (term, new_list) in additions {
            let Some(old_list) = postings.remove(&term) else {
                postings.insert(term, new_list);
                continue;
            };
            let mut merged = Vec::with_capacity(old_list.len() + new_list.len());
            let (mut old_idx, mut new_idx) = (0, 0);
            while old_idx < old_list.len() && new_idx < new_list.len() {
                if old_list[old_idx].chunk < new_list[new_idx].chunk {
                    merged.push(old_list[old_idx].clone());
                    old_idx += 1;
                } else {
                    merged.push(new_list[new_idx].clone());
                    new_idx += 1;
                }
            }
            merged.extend_from_slice(&old_list[old_idx..]);
            merged.extend_from_slice(&new_list[new_idx..]);
            postings.insert(term, merged);
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
        let ignore = dir.join(".gitignore");
        if !ignore.exists() {
            // Keep graf's generated files out of the repository's git status.
            fs::write(&ignore, "# Generated by graf\n*\n")?;
        }
        let mut temp = tempfile::NamedTempFile::new_in(&dir)?;
        {
            let mut output = BufWriter::new(temp.as_file_mut());
            bincode::serialize_into(&mut output, self)?;
            output.flush()?;
        }
        temp.as_file().sync_all()?;
        replace(temp.into_temp_path(), &index_path(&self.root))
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
fn scan(root: &Path, previous: Option<&Index>, reuse_unchanged: bool) -> Result<Scan> {
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
            Some(old_source) => {
                // Analyzer-preserving updates carry chunk metadata and postings
                // forward. Analyzer changes need fresh chunk text for retokenization.
                let source = if reuse_unchanged {
                    scan.reused_paths.insert(rel.clone());
                    old_source.clone()
                } else {
                    Source::new(&rel, &text, hash)
                };
                scan.files.insert(rel, source);
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

#[cfg(test)]
mod incremental_tests {
    use super::*;

    fn posting_values(index: &Index) -> BTreeMap<&str, Vec<(u32, u32)>> {
        index
            .postings
            .iter()
            .map(|(term, postings)| {
                (
                    term.as_str(),
                    postings
                        .iter()
                        .map(|posting| (posting.chunk, posting.count))
                        .collect(),
                )
            })
            .collect()
    }

    #[test]
    fn incremental_refresh_reuses_loaded_sources_and_matches_full_rebuild() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        fs::create_dir(root.join("src")).unwrap();
        fs::write(root.join("src/alpha.rs"), "fn alpha_cache() { cache(); }\n").unwrap();
        fs::write(root.join("src/beta.rs"), "fn beta_cache() { cache(); }\n").unwrap();
        Index::build(&root, true).unwrap();
        let previous = Index::load(&root).unwrap();
        let old_beta = previous.files["src/beta.rs"].clone();

        // The changed source is split with text for tokenization; the unchanged
        // loaded source is cloned with its schema-stored ranges and empty text.
        fs::write(
            root.join("src/alpha.rs"),
            "fn alpha_session() { refresh(); }\n",
        )
        .unwrap();
        let incremental_scan = scan(&root, Some(&previous), true).unwrap();
        assert!(incremental_scan.reused_paths.contains("src/beta.rs"));
        let beta = &incremental_scan.files["src/beta.rs"];
        assert!(beta.chunks.iter().all(|chunk| chunk.text.is_empty()));
        assert_eq!(beta.hash, old_beta.hash);
        assert_eq!(beta.chunks.len(), old_beta.chunks.len());
        assert_eq!(beta.chunks[0].id, old_beta.chunks[0].id);
        assert!(
            !incremental_scan.files["src/alpha.rs"].chunks[0]
                .text
                .is_empty()
        );

        let incremental = Index::with_postings(
            &root,
            incremental_scan.files,
            previous.analyzer,
            Some(&previous),
            &incremental_scan.reused_paths,
        );
        let full_scan = scan(&root, None, false).unwrap();
        let full = Index::with_postings(
            &root,
            full_scan.files,
            previous.analyzer,
            None,
            &full_scan.reused_paths,
        );
        assert_eq!(incremental.lengths, full.lengths);
        assert_eq!(posting_values(&incremental), posting_values(&full));
    }

    #[test]
    fn analyzer_switch_keeps_fresh_text_for_full_retokenization() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        fs::write(root.join("alpha.rs"), "fn alpha_cache() { cache(); }\n").unwrap();
        Index::build(&root, true).unwrap();
        let previous = Index::load(&root).unwrap();
        let analyzer_scan = scan(&root, Some(&previous), false).unwrap();
        assert!(analyzer_scan.reused_paths.is_empty());
        assert!(
            analyzer_scan.files["alpha.rs"].chunks[0]
                .text
                .contains("alpha_cache")
        );

        let analyzer = Analyzer {
            stopwords: crate::terms::Stopwords::None,
            full_identifiers: true,
            path_weight: 2,
        };
        let rebuilt = Index::with_postings(
            &root,
            analyzer_scan.files,
            analyzer,
            None,
            &analyzer_scan.reused_paths,
        );
        assert!(rebuilt.postings.contains_key("alpha_cache"));
    }
}
