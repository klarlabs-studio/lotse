//! Deterministic lexical retrieval over versioned repository source excerpts.
use anyhow::{Context, Result, bail, ensure};
use ignore::WalkBuilder;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::io::{BufWriter, Write};
use std::path::{Component, Path, PathBuf};
use tiktoken_rs::{CoreBPE, cl100k_base};

const SCHEMA: u32 = 1;
const CHUNK_LINES: usize = 24;
const MAX_FILE_BYTES: u64 = 1_000_000;
const MIN_HANDLE: usize = 12;
const STOP: &str = "a an the is are be to of for and or in on with its it this that which what where how why can as by from if should does do need find locate explain check claim against changes change implementation test tests";

pub fn digest(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

/// Neighbors in sorted hash order determine the shortest unique prefix.
/// Consider the entire index so scopes and response budgets cannot change handles.
fn compact_handles<'a>(ids: impl Iterator<Item = &'a str>) -> BTreeMap<&'a str, &'a str> {
    let mut ids: Vec<_> = ids.collect();
    ids.sort_unstable();
    ids.iter()
        .enumerate()
        .map(|(i, &id)| {
            let required = [i.checked_sub(1), (i + 1 < ids.len()).then_some(i + 1)]
                .into_iter()
                .flatten()
                .map(|j| {
                    id.bytes()
                        .zip(ids[j].bytes())
                        .take_while(|(a, b)| a == b)
                        .count()
                        + 1
                })
                .max()
                .unwrap_or(0)
                .max(MIN_HANDLE)
                .min(id.len());
            (id, &id[..required])
        })
        .collect()
}

/// Split identifier boundaries as well as punctuation. No learned query rewriting.
pub fn terms(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut split = String::new();
    for (i, &c) in chars.iter().enumerate() {
        if i > 0
            && c.is_uppercase()
            && (chars[i - 1].is_lowercase()
                || chars[i - 1].is_numeric()
                || (chars[i - 1].is_uppercase()
                    && chars.get(i + 1).is_some_and(|n| n.is_lowercase())))
        {
            split.push(' ');
        }
        split.push(c);
    }
    split
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(str::to_lowercase)
        .filter(|s| !STOP.split_whitespace().any(|stop| stop == s))
        .collect()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Chunk {
    pub id: String,
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Source {
    pub hash: String,
    pub bytes: usize,
    pub lines: usize,
    pub kind: String,
    pub chunks: Vec<Chunk>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Posting {
    pub chunk: usize,
    pub count: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Index {
    pub schema: u32,
    pub root: PathBuf,
    pub files: BTreeMap<String, Source>,
    pub postings: BTreeMap<String, Vec<Posting>>,
    pub lengths: Vec<usize>,
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

fn source_kind(path: &str) -> &'static str {
    let extension = Path::new(path)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    if matches!(extension, "md" | "mdx" | "rst" | "txt" | "adoc") {
        "docs"
    } else if path
        .split('/')
        .any(|p| matches!(p, "test" | "tests" | "__tests__"))
        || path.contains(".test.")
        || path.contains(".spec.")
        || path.ends_with("_test.go")
        || Path::new(path)
            .file_name()
            .is_some_and(|p| p.to_string_lossy().starts_with("test_"))
    {
        "test"
    } else {
        "code"
    }
}

fn supported(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|s| s.to_str()),
        Some(
            "rs" | "go"
                | "py"
                | "js"
                | "jsx"
                | "ts"
                | "tsx"
                | "mjs"
                | "cjs"
                | "java"
                | "kt"
                | "kts"
                | "c"
                | "h"
                | "cpp"
                | "hpp"
                | "cc"
                | "cs"
                | "rb"
                | "php"
                | "swift"
                | "scala"
                | "sh"
                | "bash"
                | "sql"
                | "proto"
                | "md"
                | "mdx"
                | "rst"
                | "txt"
                | "adoc"
                | "toml"
                | "yaml"
                | "yml"
                | "json"
                | "html"
                | "css"
                | "scss"
                | "vue"
                | "svelte"
        )
    ) || matches!(
        path.file_name().and_then(|s| s.to_str()),
        Some("Dockerfile" | "Makefile")
    )
}

fn make_source(path: &str, text: &str) -> Source {
    let hash = digest(text);
    let lines: Vec<&str> = text.lines().collect();
    let chunks = lines
        .chunks(CHUNK_LINES)
        .enumerate()
        .filter_map(|(i, lines)| {
            let text = lines.join("\n");
            if text.trim().is_empty() {
                return None;
            }
            let start = i * CHUNK_LINES + 1;
            let end = start + lines.len() - 1;
            let id = digest(&format!("{path}\0{hash}\0{start}\0{end}"));
            Some(Chunk {
                id,
                path: path.into(),
                start,
                end,
                text,
            })
        })
        .collect();
    Source {
        hash,
        bytes: text.len(),
        lines: lines.len(),
        kind: source_kind(path).into(),
        chunks,
    }
}

pub fn index_path(root: &Path) -> PathBuf {
    root.join(".graf/index.json")
}

impl Index {
    pub fn load(root: &Path) -> Result<Self> {
        let index: Self = serde_json::from_slice(
            &fs::read(index_path(root)).context("No readable index; run `graf index` first")?,
        )
        .context("Invalid index; rebuild with `graf index --rebuild`")?;
        ensure!(
            index.schema == SCHEMA,
            "Unsupported index schema; run `graf index --rebuild`"
        );
        ensure!(
            index.root == root,
            "Index belongs to another root; run `graf index --rebuild`"
        );
        let chunk_count: usize = index.files.values().map(|s| s.chunks.len()).sum();
        ensure!(
            index.lengths.len() == chunk_count,
            "Invalid chunk counts; run `graf index --rebuild`"
        );
        ensure!(
            index
                .postings
                .values()
                .flatten()
                .all(|p| p.chunk < chunk_count && p.count > 0),
            "Invalid postings; run `graf index --rebuild`"
        );
        ensure!(
            index.files.iter().all(
                |(path, source)| source.chunks.iter().all(|c| c.path == *path
                    && c.start > 0
                    && c.start <= c.end
                    && c.end <= source.lines)
            ),
            "Invalid source ranges; run `graf index --rebuild`"
        );
        Ok(index)
    }

    pub fn build(root: &Path, rebuild: bool) -> Result<BuildReport> {
        let previous = if !rebuild && index_path(root).exists() {
            Some(Self::load(root)?)
        } else {
            None
        };
        let mut files = BTreeMap::new();
        let (mut changed, mut reused, mut skipped) = (0, 0, 0);
        // Respect repository/global ignore rules even in a directory without .git.
        let mut walk = WalkBuilder::new(root);
        walk.parents(false)
            .hidden(true)
            .follow_links(false)
            .require_git(false)
            .filter_entry(|entry| {
                entry.depth() == 0
                    || !entry.file_type().is_some_and(|t| t.is_dir())
                    || !matches!(
                        entry.file_name().to_str(),
                        Some("node_modules" | "vendor" | "target" | "dist" | "__pycache__")
                    )
            });
        for entry in walk.build() {
            let entry = entry.context("Cannot traverse repository")?;
            if !entry.file_type().is_some_and(|t| t.is_file()) || !supported(entry.path()) {
                continue;
            }
            if entry.metadata()?.len() > MAX_FILE_BYTES {
                skipped += 1;
                continue;
            }
            let text = match fs::read_to_string(entry.path()) {
                Ok(text) if !text.contains('\0') && text.len() <= MAX_FILE_BYTES as usize => text,
                Ok(_) => {
                    skipped += 1;
                    continue;
                }
                Err(e) if e.kind() == std::io::ErrorKind::InvalidData => {
                    skipped += 1;
                    continue;
                }
                Err(e) => return Err(e).context("Cannot read source file"),
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
            if let Some(old) = previous
                .as_ref()
                .and_then(|p| p.files.get(&rel))
                .filter(|s| s.hash == digest(&text))
            {
                files.insert(rel, old.clone());
                reused += 1;
            } else {
                files.insert(rel.clone(), make_source(&rel, &text));
                changed += 1;
            }
        }
        let deleted = previous.as_ref().map_or(0, |p| {
            p.files.keys().filter(|p| !files.contains_key(*p)).count()
        });
        let mut index = Self {
            schema: SCHEMA,
            root: root.into(),
            files,
            postings: BTreeMap::new(),
            lengths: Vec::new(),
        };
        for source in index.files.values() {
            for chunk in &source.chunks {
                let mut counts = BTreeMap::new();
                for term in terms(&format!("{}\n{}", chunk.path, chunk.text)) {
                    *counts.entry(term).or_insert(0usize) += 1;
                }
                let id = index.lengths.len();
                index.lengths.push(counts.values().sum());
                for (term, count) in counts {
                    index
                        .postings
                        .entry(term)
                        .or_default()
                        .push(Posting { chunk: id, count });
                }
            }
        }
        let dir = root.join(".graf");
        fs::create_dir_all(&dir)?;
        ensure!(
            !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
            ".graf must not be a symlink"
        );
        let mut temp = tempfile::NamedTempFile::new_in(&dir)?;
        {
            let mut output = BufWriter::new(temp.as_file_mut());
            serde_json::to_writer(&mut output, &index)?;
            output.flush()?;
        }
        temp.as_file().sync_all()?;
        temp.persist(index_path(root))
            .map_err(|e| e.error)
            .context("Cannot save index atomically")?;
        Ok(BuildReport {
            changed_files: changed,
            reused_files: reused,
            deleted_files: deleted,
            skipped_files: skipped,
            stats: index.stats()?,
        })
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

    fn current_text(&self, path: &str) -> Result<String> {
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
        let lines: Vec<_> = text.lines().collect();
        ensure!(
            source.chunks.iter().all(|chunk| chunk.end <= lines.len()
                && lines[chunk.start - 1..chunk.end].join("\n") == chunk.text),
            "Corrupt source excerpts for {path}; run `graf index --rebuild`"
        );
        Ok(text)
    }

    fn ranked_chunks(
        &self,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Vec<(&Chunk, f64)>> {
        let chunks: Vec<&Chunk> = self.files.values().flat_map(|f| &f.chunks).collect();
        ensure!(!query.trim().is_empty(), "Query must not be empty");
        let mut scores = HashMap::<usize, f64>::new();
        let average = self.lengths.iter().sum::<usize>() as f64 / self.lengths.len().max(1) as f64;
        for term in terms(query).into_iter().collect::<BTreeSet<_>>() {
            if let Some(hits) = self.postings.get(&term) {
                let idf = (1.0
                    + (chunks.len() as f64 - hits.len() as f64 + 0.5) / (hits.len() as f64 + 0.5))
                    .ln();
                for hit in hits {
                    let count = hit.count as f64;
                    let norm =
                        1.2 * (0.25 + 0.75 * self.lengths[hit.chunk] as f64 / average.max(1.0));
                    *scores.entry(hit.chunk).or_default() += idf * count * 2.2 / (count + norm);
                }
            }
        }
        let mut ranking: Vec<_> = scores
            .into_iter()
            .filter(|(i, _)| {
                let chunk = chunks[*i];
                scope.is_none_or(|prefix| chunk.path.starts_with(prefix))
                    && kind.is_none_or(|kind| self.files[&chunk.path].kind == kind)
            })
            .collect();
        ranking.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
        Ok(ranking
            .into_iter()
            .map(|(i, score)| (chunks[i], score))
            .collect())
    }

    pub fn search(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Response> {
        let ranking = self.ranked_chunks(query, scope, kind)?;
        let matched = ranking.len();
        let mut response = Response::new(budget, matched);
        let bpe = cl100k_base()?;
        let mut checked = BTreeSet::new();
        for (chunk, score) in ranking.into_iter().take(100) {
            if response.excerpts.len() >= limit {
                break;
            }
            if checked.insert(chunk.path.clone()) {
                self.current_text(&chunk.path)?;
            }
            response.try_push(
                chunk.clone(),
                &self.files[&chunk.path].hash,
                Some(score),
                &bpe,
            );
        }
        response.finish();
        Ok(response)
    }

    /// Return one best-scoring source anchor per file without changing BM25 scoring.
    pub fn locate(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<LocateResponse> {
        ensure!(
            (1..=1000).contains(&limit),
            "limit must be between 1 and 1000"
        );
        let mut seen = BTreeSet::new();
        let files: Vec<_> = self
            .ranked_chunks(query, scope, kind)?
            .into_iter()
            .filter(|(chunk, _)| seen.insert(chunk.path.clone()))
            .collect();
        let mut response = LocateResponse {
            status: String::new(),
            output_kind: "navigation_hints",
            tokenizer: "cl100k_base",
            budget_scope: "context (including paths, handles, previews); JSON metadata excluded",
            budget,
            context_tokens: 0,
            matched_files: files.len(),
            hints: Vec::new(),
            context: String::new(),
        };
        let query_terms: BTreeSet<_> = terms(query).into_iter().collect();
        let handles = compact_handles(
            self.files
                .values()
                .flat_map(|source| source.chunks.iter().map(|c| c.id.as_str())),
        );
        let bpe = cl100k_base()?;
        for (chunk, score) in files.into_iter().take(100) {
            if response.hints.len() >= limit {
                break;
            }
            self.current_text(&chunk.path)?;
            // Prefer a line with more distinct query terms; ties keep source order.
            let mut best = (0, 0, "");
            for (offset, line) in chunk.text.lines().enumerate() {
                let matches = terms(line)
                    .into_iter()
                    .collect::<BTreeSet<_>>()
                    .intersection(&query_terms)
                    .count();
                if offset == 0 || matches > best.0 {
                    best = (matches, offset, line);
                }
            }
            let line = chunk.start + best.1;
            let preview: String = best.2.trim().chars().take(160).collect();
            let preview_truncated = best.2.trim().chars().count() > 160;
            let path = serde_json::to_string(&chunk.path)?;
            let quoted = serde_json::to_string(&preview)?;
            let section = format!(
                "{path}:{}-{}\nid={}\nL{line}: {quoted}{}",
                chunk.start,
                chunk.end,
                handles[chunk.id.as_str()],
                if preview_truncated {
                    " [truncated]"
                } else {
                    ""
                }
            );
            let candidate = if response.context.is_empty() {
                section
            } else {
                format!("{}\n\n{section}", response.context)
            };
            let count = bpe.encode_ordinary(&candidate).len();
            if count > budget {
                continue;
            }
            response.context = candidate;
            response.context_tokens = count;
            response.hints.push(Hint {
                id: handles[chunk.id.as_str()].to_string(),
                path: chunk.path.clone(),
                start: chunk.start,
                end: chunk.end,
                line,
                preview,
                preview_truncated,
                source_hash: self.files[&chunk.path].hash.clone(),
                score,
            });
        }
        response.status = if response.matched_files == 0 {
            "no_lexical_match"
        } else if response.hints.is_empty() {
            "budget_exhausted"
        } else {
            "ok"
        }
        .into();
        Ok(response)
    }

    pub fn expand(&self, id: &str, before: usize, after: usize, budget: usize) -> Result<Response> {
        ensure!(
            (MIN_HANDLE..=64).contains(&id.len())
                && id
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
            "Excerpt ID must be 12 to 64 lowercase hex characters"
        );
        let mut matches = self
            .files
            .values()
            .flat_map(|s| &s.chunks)
            .filter(|c| c.id.starts_with(id));
        let chunk = matches
            .next()
            .context("Unknown excerpt ID; search again after indexing")?;
        ensure!(
            matches.next().is_none(),
            "Ambiguous excerpt ID; use a longer handle"
        );
        let text = self.current_text(&chunk.path)?;
        let lines: Vec<&str> = text.lines().collect();
        let start = chunk.start.saturating_sub(before).max(1);
        let end = chunk.end.saturating_add(after).min(lines.len());
        let expanded = Chunk {
            start,
            end,
            text: lines[start - 1..end].join("\n"),
            ..chunk.clone()
        };
        let mut response = Response::new(budget, 1);
        response.try_push(
            expanded,
            &self.files[&chunk.path].hash,
            None,
            &cl100k_base()?,
        );
        response.finish();
        Ok(response)
    }
}

#[derive(Debug, Serialize)]
pub struct Hint {
    pub id: String,
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub line: usize,
    pub preview: String,
    pub preview_truncated: bool,
    pub source_hash: String,
    pub score: f64,
}

#[derive(Debug, Serialize)]
pub struct LocateResponse {
    pub status: String,
    pub output_kind: &'static str,
    pub tokenizer: &'static str,
    pub budget_scope: &'static str,
    pub budget: usize,
    pub context_tokens: usize,
    pub matched_files: usize,
    pub hints: Vec<Hint>,
    pub context: String,
}

#[derive(Debug, Serialize)]
pub struct Excerpt {
    /// Anchor ID usable with expand; expansion retains the search anchor ID.
    pub id: String,
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub source_hash: String,
    pub score: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct Response {
    pub status: String,
    pub tokenizer: &'static str,
    pub budget_scope: &'static str,
    pub budget: usize,
    pub context_tokens: usize,
    pub matched_chunks: usize,
    pub excerpts: Vec<Excerpt>,
    pub context: String,
}

impl Response {
    fn new(budget: usize, matched_chunks: usize) -> Self {
        Self {
            status: String::new(),
            tokenizer: "cl100k_base",
            budget_scope: "context (including citation headers); JSON metadata excluded",
            budget,
            context_tokens: 0,
            matched_chunks,
            excerpts: Vec::new(),
            context: String::new(),
        }
    }
    fn try_push(&mut self, chunk: Chunk, hash: &str, score: Option<f64>, bpe: &CoreBPE) {
        // JSON escaping keeps malicious/newline filenames on one header line.
        let header = serde_json::to_string(&chunk.path).expect("String serialization");
        let section = format!("{header}:{}-{}\n{}", chunk.start, chunk.end, chunk.text);
        let candidate = if self.context.is_empty() {
            section
        } else {
            format!("{}\n\n{section}", self.context)
        };
        let count = bpe.encode_ordinary(&candidate).len();
        if count > self.budget {
            return;
        }
        self.context = candidate;
        self.context_tokens = count;
        self.excerpts.push(Excerpt {
            id: chunk.id,
            path: chunk.path,
            start: chunk.start,
            end: chunk.end,
            source_hash: hash.into(),
            score,
        });
    }
    fn finish(&mut self) {
        self.status = if self.matched_chunks == 0 {
            "no_lexical_match"
        } else if self.excerpts.is_empty() {
            "budget_exhausted"
        } else {
            "ok"
        }
        .into();
    }
}

pub fn canonical_root(root: &Path) -> Result<PathBuf> {
    let root = root
        .canonicalize()
        .context("Repository root does not exist")?;
    if !root.is_dir() {
        bail!("Repository root must be a directory");
    }
    Ok(root)
}

#[cfg(test)]
mod handle_tests {
    use super::*;

    #[test]
    fn prefixes_extend_past_collisions_and_ignore_input_order() {
        let a = format!("{}0{}", "a".repeat(12), "0".repeat(51));
        let b = format!("{}1{}", "a".repeat(12), "0".repeat(51));
        let c = "b".repeat(64);
        let ids = [a.as_str(), b.as_str(), c.as_str()];
        let handles = compact_handles(ids.into_iter());
        assert_eq!(handles[a.as_str()], &a[..13]);
        assert_eq!(handles[b.as_str()], &b[..13]);
        assert_eq!(handles[c.as_str()], &c[..12]);
        assert_eq!(handles, compact_handles(ids.into_iter().rev()));
    }
}
