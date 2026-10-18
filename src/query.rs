//! BM25 ranking and budgeted delivery: search, locate, and expand.
use crate::handles::{MIN_HANDLE, compact_handles};
use crate::index::Index;
use crate::pack::Packer;
use crate::policy::{Policy, Stop};
use crate::response::{Excerpt, Hint, LocateResponse, Response, TOKENIZER, status};
use crate::source::Chunk;
use crate::terms::terms;
use anyhow::{Context, Result, ensure};
use std::collections::{BTreeSet, HashMap};
use tiktoken_rs::cl100k_base;

const K1: f64 = 1.2;
const B: f64 = 0.75;
/// Ranked candidates (chunks for search, files for locate) considered for packing.
const MAX_CANDIDATES: usize = 100;
const MAX_LIMIT: usize = 1000;
const PREVIEW_CHARS: usize = 160;
const SEARCH_SCOPE: &str = "context (including citation headers); JSON metadata excluded";
const LOCATE_SCOPE: &str = "context (including paths, handles, previews); JSON metadata excluded";

fn ensure_limit(limit: usize) -> Result<()> {
    ensure!(
        (1..=MAX_LIMIT).contains(&limit),
        "limit must be between 1 and {MAX_LIMIT}"
    );
    Ok(())
}

/// The leading ranked candidates offered to the packer under a stopping rule.
fn candidates<T>(ranking: Vec<(T, f64)>, stop: Stop) -> Vec<(T, f64)> {
    let mut candidates: Vec<_> = ranking.into_iter().take(MAX_CANDIDATES).collect();
    let scores: Vec<f64> = candidates.iter().map(|(_, score)| *score).collect();
    candidates.truncate(stop.keep(&scores));
    candidates
}

/// `"path":start-end`. JSON escaping keeps malicious/newline filenames on one line.
fn citation(path: &str, start: usize, end: usize) -> Result<String> {
    Ok(format!("{}:{start}-{end}", serde_json::to_string(path)?))
}

/// The anchor line with the most distinct query terms; ties keep source order.
fn best_line<'a>(chunk: &'a Chunk, query_terms: &BTreeSet<String>) -> (usize, &'a str) {
    let mut best = (0, 0, "");
    for (offset, line) in chunk.text.lines().enumerate() {
        let matches = terms(line)
            .into_iter()
            .collect::<BTreeSet<_>>()
            .intersection(query_terms)
            .count();
        if offset == 0 || matches > best.0 {
            best = (matches, offset, line);
        }
    }
    (best.1, best.2)
}

impl Index {
    fn ranked_chunks(
        &self,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Vec<(&Chunk, f64)>> {
        ensure!(!query.trim().is_empty(), "Query must not be empty");
        let chunks: Vec<&Chunk> = self.chunks().collect();
        let mut scores = HashMap::<usize, f64>::new();
        let average = self.lengths.iter().sum::<usize>() as f64 / self.lengths.len().max(1) as f64;
        for term in terms(query).into_iter().collect::<BTreeSet<_>>() {
            let Some(hits) = self.postings.get(&term) else {
                continue;
            };
            let idf = (1.0
                + (chunks.len() as f64 - hits.len() as f64 + 0.5) / (hits.len() as f64 + 0.5))
                .ln();
            for hit in hits {
                let count = hit.count as f64;
                let norm = K1 * (1.0 - B + B * self.lengths[hit.chunk] as f64 / average.max(1.0));
                *scores.entry(hit.chunk).or_default() += idf * count * (K1 + 1.0) / (count + norm);
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

    fn excerpt(&self, chunk: &Chunk, start: usize, end: usize, score: Option<f64>) -> Excerpt {
        Excerpt {
            id: chunk.id.clone(),
            path: chunk.path.clone(),
            start,
            end,
            source_hash: self.files[&chunk.path].hash.clone(),
            score,
        }
    }

    pub fn search(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Response> {
        self.search_with(query, budget, limit, scope, kind, Policy::default())
    }

    /// `search` under an experimental delivery policy.
    pub fn search_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<Response> {
        ensure_limit(limit)?;
        let ranking = self.ranked_chunks(query, scope, kind)?;
        let matched_chunks = ranking.len();
        let bpe = cl100k_base()?;
        let mut packer = Packer::new(&bpe, budget);
        let mut excerpts = Vec::new();
        let mut checked = BTreeSet::new();
        for (chunk, score) in candidates(ranking, policy.stop) {
            if excerpts.len() >= limit {
                break;
            }
            if checked.insert(chunk.path.as_str()) {
                self.current_text(&chunk.path)?;
            }
            let section = format!(
                "{}\n{}",
                citation(&chunk.path, chunk.start, chunk.end)?,
                chunk.text
            );
            if packer.try_push(&section) {
                excerpts.push(self.excerpt(chunk, chunk.start, chunk.end, Some(score)));
            }
        }
        Ok(Response {
            status: status(matched_chunks, excerpts.len()),
            tokenizer: TOKENIZER,
            budget_scope: SEARCH_SCOPE,
            budget,
            context_tokens: packer.tokens(),
            matched_chunks,
            excerpts,
            context: packer.into_context(),
        })
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
        self.locate_with(query, budget, limit, scope, kind, Policy::default())
    }

    /// `locate` under an experimental delivery policy.
    pub fn locate_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<LocateResponse> {
        ensure_limit(limit)?;
        let files = policy
            .file_score
            .files(self.ranked_chunks(query, scope, kind)?);
        let matched_files = files.len();
        let query_terms: BTreeSet<_> = terms(query).into_iter().collect();
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let mut packer = Packer::new(&bpe, budget);
        let mut hints = Vec::new();
        for (chunk, score) in candidates(files, policy.stop) {
            if hints.len() >= limit {
                break;
            }
            self.current_text(&chunk.path)?;
            let (offset, line) = best_line(chunk, &query_terms);
            let line_number = chunk.start + offset;
            let preview: String = line.trim().chars().take(PREVIEW_CHARS).collect();
            let preview_truncated = line.trim().chars().count() > PREVIEW_CHARS;
            let handle = handles[chunk.id.as_str()];
            let section = format!(
                "{}\nid={handle}\nL{line_number}: {}{}",
                citation(&chunk.path, chunk.start, chunk.end)?,
                serde_json::to_string(&preview)?,
                if preview_truncated {
                    " [truncated]"
                } else {
                    ""
                }
            );
            if !packer.try_push(&section) {
                continue;
            }
            hints.push(Hint {
                id: handle.to_string(),
                path: chunk.path.clone(),
                start: chunk.start,
                end: chunk.end,
                line: line_number,
                preview,
                preview_truncated,
                source_hash: self.files[&chunk.path].hash.clone(),
                score,
            });
        }
        Ok(LocateResponse {
            status: status(matched_files, hints.len()),
            output_kind: "navigation_hints",
            tokenizer: TOKENIZER,
            budget_scope: LOCATE_SCOPE,
            budget,
            context_tokens: packer.tokens(),
            matched_files,
            hints,
            context: packer.into_context(),
        })
    }

    pub fn expand(&self, id: &str, before: usize, after: usize, budget: usize) -> Result<Response> {
        ensure!(
            (MIN_HANDLE..=64).contains(&id.len())
                && id
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
            "Excerpt ID must be 12 to 64 lowercase hex characters"
        );
        let mut matches = self.chunks().filter(|c| c.id.starts_with(id));
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
        let section = format!(
            "{}\n{}",
            citation(&chunk.path, start, end)?,
            lines[start - 1..end].join("\n")
        );
        let bpe = cl100k_base()?;
        let mut packer = Packer::new(&bpe, budget);
        let excerpts = if packer.try_push(&section) {
            vec![self.excerpt(chunk, start, end, None)]
        } else {
            Vec::new()
        };
        Ok(Response {
            status: status(1, excerpts.len()),
            tokenizer: TOKENIZER,
            budget_scope: SEARCH_SCOPE,
            budget,
            context_tokens: packer.tokens(),
            matched_chunks: 1,
            excerpts,
            context: packer.into_context(),
        })
    }
}
