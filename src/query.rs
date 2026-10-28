//! BM25 ranking and budgeted delivery: search, locate, and expand.
use crate::handles::{MIN_HANDLE, compact_handles};
use crate::index::Index;
use crate::pack::Packer;
use crate::policy::{Policy, Stop};
use crate::response::{
    EvidenceResponse, Excerpt, Hint, LocateResponse, Response, TOKENIZER, status,
};
use crate::source::Chunk;
use crate::terms::Analyzer;
use anyhow::{Context, Result, ensure};
use std::collections::{BTreeMap, BTreeSet, HashMap};
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
fn best_line<'a>(
    text: &'a str,
    query_terms: &BTreeSet<String>,
    analyzer: &Analyzer,
) -> (usize, &'a str) {
    let mut best = (0, 0, "");
    for (offset, line) in text.lines().enumerate() {
        let matches = analyzer
            .terms(line)
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

/// Conservative lexical alternatives: ASCII words, fixed prefix, bounded suffix edits.
fn family_distance(a: &str, b: &str) -> Option<usize> {
    if ![a, b]
        .iter()
        .all(|s| (5..=64).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_alphabetic()))
        || a[..4] != b[..4]
    {
        return None;
    }
    let maximum = if a.len().min(b.len()) >= 7 { 2 } else { 1 };
    if a.len().abs_diff(b.len()) > maximum {
        return None;
    }
    let mut previous = [0usize; 65];
    for (j, item) in previous.iter_mut().enumerate().take(b.len() + 1) {
        *item = j;
    }
    for (i, x) in a.bytes().enumerate() {
        let mut current = [0usize; 65];
        current[0] = i + 1;
        for (j, y) in b.bytes().enumerate() {
            current[j + 1] = (previous[j] + usize::from(x != y))
                .min(previous[j + 1] + 1)
                .min(current[j] + 1);
        }
        previous = current;
    }
    (previous[b.len()] <= maximum).then_some(previous[b.len()])
}

impl Index {
    fn ranked_chunks(
        &self,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Vec<(&Chunk, f64)>> {
        self.ranked_chunks_coverage(query, scope, kind, false)
    }

    /// Opt-in coverage factor uses distinct indexed query terms, never term frequency.
    fn ranked_chunks_coverage(
        &self,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
        coverage: bool,
    ) -> Result<Vec<(&Chunk, f64)>> {
        ensure!(!query.trim().is_empty(), "Query must not be empty");
        let chunks: Vec<&Chunk> = self.chunks().collect();
        let mut scores = HashMap::<usize, f64>::new();
        let mut support = HashMap::<usize, f64>::new();
        let mut query_weight = 0.0;

        let average = self.lengths.iter().map(|&l| l as usize).sum::<usize>() as f64
            / self.lengths.len().max(1) as f64;
        for term in self
            .analyzer
            .terms(query)
            .into_iter()
            .collect::<BTreeSet<_>>()
        {
            let Some(hits) = self.postings.get(&term) else {
                continue;
            };
            let idf = (1.0
                + (chunks.len() as f64 - hits.len() as f64 + 0.5) / (hits.len() as f64 + 0.5))
                .ln();
            if coverage {
                query_weight += idf;
            }
            for hit in hits {
                if coverage {
                    *support.entry(hit.chunk as usize).or_default() += idf;
                }
                let count = hit.count as f64;
                let norm =
                    K1 * (1.0 - B + B * self.lengths[hit.chunk as usize] as f64 / average.max(1.0));
                *scores.entry(hit.chunk as usize).or_default() +=
                    idf * count * (K1 + 1.0) / (count + norm);
            }
        }
        if coverage && query_weight > 0.0 {
            for (i, score) in &mut scores {
                *score *= (support[i] / query_weight).powi(2);
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

    /// At most four indexed alternatives, ordered by edit distance then spelling.
    /// Exact terms retain weight one; alternatives never add their frequencies.
    fn term_family(&self, term: &str) -> Vec<(String, usize)> {
        let mut alternatives = Vec::new();
        if (5..=64).contains(&term.len()) && term.bytes().all(|c| c.is_ascii_alphabetic()) {
            let prefix = &term[..4];
            for (word, _) in self.postings.range(prefix.to_string()..) {
                if !word.starts_with(prefix) {
                    break;
                }
                if word != term
                    && let Some(distance) = family_distance(term, word)
                {
                    alternatives.push((word.clone(), distance));
                }
            }
        }
        alternatives.sort_by(|a, b| a.1.cmp(&b.1).then(a.0.cmp(&b.0)));
        alternatives.truncate(4);
        if self.postings.contains_key(term) {
            alternatives.insert(0, (term.to_string(), 0));
        }
        alternatives
    }

    fn ranked_family_chunks(
        &self,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Vec<(&Chunk, f64)>> {
        ensure!(!query.trim().is_empty(), "Query must not be empty");
        let chunks: Vec<_> = self.chunks().collect();
        let average = self.lengths.iter().map(|&n| n as usize).sum::<usize>() as f64
            / self.lengths.len().max(1) as f64;
        let mut scores = HashMap::<usize, f64>::new();
        let mut support = HashMap::<usize, f64>::new();
        let mut query_weight = 0.0;
        for term in self
            .analyzer
            .terms(query)
            .into_iter()
            .collect::<BTreeSet<_>>()
        {
            let mut group = HashMap::<usize, f64>::new();
            for (word, distance) in self.term_family(&term) {
                for hit in &self.postings[&word] {
                    let count = hit.count as f64 * 0.5f64.powi(distance as i32);
                    let best = group.entry(hit.chunk as usize).or_default();
                    *best = best.max(count);
                }
            }
            if group.is_empty() {
                continue;
            }
            let idf = (1.0
                + (chunks.len() as f64 - group.len() as f64 + 0.5) / (group.len() as f64 + 0.5))
                .ln();
            query_weight += idf;
            for (i, count) in group {
                let norm = K1 * (1.0 - B + B * self.lengths[i] as f64 / average.max(1.0));
                *scores.entry(i).or_default() += idf * count * (K1 + 1.0) / (count + norm);
                *support.entry(i).or_default() += idf;
            }
        }
        let mut ranking: Vec<_> = scores
            .into_iter()
            .filter_map(|(i, score)| {
                let chunk = chunks[i];
                (scope.is_none_or(|prefix| chunk.path.starts_with(prefix))
                    && kind.is_none_or(|k| self.files[&chunk.path].kind == k))
                .then_some((i, score * (support[&i] / query_weight).powi(2)))
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

    /// MCP delivers context alone, so include expansion handles in its token budget.
    /// Repack the selected excerpts as whole sections; handles may displace an excerpt.
    pub(crate) fn search_for_agent(&self, query: &str, budget: usize) -> Result<String> {
        let response = self.search(query, budget, MAX_LIMIT, None, None)?;
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let mut packer = Packer::new(&bpe, budget);
        let mut texts = HashMap::new();
        for excerpt in response.excerpts {
            if !texts.contains_key(&excerpt.path) {
                texts.insert(excerpt.path.clone(), self.current_text(&excerpt.path)?);
            }
            let text = texts[&excerpt.path]
                .lines()
                .skip(excerpt.start - 1)
                .take(excerpt.end - excerpt.start + 1)
                .collect::<Vec<_>>()
                .join("\n");
            packer.try_push(&format!(
                "{}\nid={}\n{}",
                citation(&excerpt.path, excerpt.start, excerpt.end)?,
                handles[excerpt.id.as_str()],
                text
            ));
        }
        Ok(packer.into_context())
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
        let mut texts = HashMap::<&str, String>::new();
        for (chunk, score) in candidates(ranking, policy.stop) {
            if excerpts.len() >= limit {
                break;
            }
            if !texts.contains_key(chunk.path.as_str()) {
                texts.insert(&chunk.path, self.current_text(&chunk.path)?);
            }
            let section = format!(
                "{}\n{}",
                citation(&chunk.path, chunk.start, chunk.end)?,
                Index::excerpt_text(&texts[chunk.path.as_str()], chunk)
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
        self.locate_ranked(
            query,
            budget,
            limit,
            self.ranked_chunks(query, scope, kind)?,
            policy,
        )
    }

    fn locate_ranked(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        ranking: Vec<(&Chunk, f64)>,
        policy: Policy,
    ) -> Result<LocateResponse> {
        let files = policy.file_score.files(ranking);
        let matched_files = files.len();
        let query_terms: BTreeSet<_> = self.analyzer.terms(query).into_iter().collect();
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let mut packer = Packer::new(&bpe, budget);
        let mut hints = Vec::new();
        for (chunk, score) in candidates(files, policy.stop) {
            if hints.len() >= limit {
                break;
            }
            let excerpt = Index::excerpt_text(&self.current_text(&chunk.path)?, chunk);
            let (offset, line) = best_line(&excerpt, &query_terms, &self.analyzer);
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

    /// Experimental locate-first source delivery with an equal per-file budget.
    /// Select at most `limit` hints under the same budget and policy as locate.
    /// Around each anchor retain up to 24 physical lines, starting eight before;
    /// trim the farther endpoint (end on ties) until the complete range fits its
    /// share. Never split source lines or spend another file's reserved share.
    pub fn evidence_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let located = self.locate_with(query, budget, limit, scope, kind, policy)?;
        self.evidence_from(located, query, budget, false)
    }

    /// Experimental complete structural units within locate-ranked files.
    pub fn evidence_units_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let located = self.locate_with(query, budget, limit, scope, kind, policy)?;
        self.evidence_from(located, query, budget, true)
    }

    /// Experimental BM25 multiplied by squared IDF-weighted query coverage.
    /// Only file selection changes; source units and budget allocation stay fixed.
    pub fn evidence_coverage_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        ensure_limit(limit)?;
        let ranking = self.ranked_chunks_coverage(query, scope, kind, true)?;
        let located = self.locate_ranked(query, budget, limit, ranking, policy)?;
        self.evidence_from(located, query, budget, true)
    }

    /// Experimental bounded lexical families with grouped BM25 and query coverage.
    pub fn evidence_families_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        ensure_limit(limit)?;
        let ranking = self.ranked_family_chunks(query, scope, kind)?;
        let located = self.locate_ranked(query, budget, limit, ranking, policy)?;
        self.evidence_from(located, query, budget, true)
    }

    fn ranked_fused_files(
        &self,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
        references: bool,
    ) -> Result<Vec<(&Chunk, f64)>> {
        let ranking = self.ranked_family_chunks(query, scope, kind)?;
        let family = policy.file_score.files(ranking);
        let indexed = crate::triage::indexed_units(self)?;
        let body = crate::units::unit_scores(&indexed.bm25, &indexed.edges, query);
        let scores = crate::units::symbol_scores(&indexed.units, &body, query);
        // Aggregate by best unit, with stable unit order for equal scores.
        let mut units: Vec<_> = indexed
            .units
            .iter()
            .zip(scores)
            .filter(|(unit, score)| {
                *score > 0.0
                    && scope.is_none_or(|prefix| unit.path.starts_with(prefix))
                    && kind.is_none_or(|k| self.files[&unit.path].kind == k)
            })
            .collect();
        units.sort_by(|a, b| b.1.total_cmp(&a.1));
        let mut seen = BTreeSet::new();
        let mut fused: BTreeMap<&str, (&Chunk, f64)> = BTreeMap::new();
        for (rank, (chunk, _)) in family.into_iter().enumerate() {
            fused.insert(&chunk.path, (chunk, 1.0 / (60.0 + (rank + 1) as f64)));
        }
        let mut rank = 0;
        for (unit, _) in units {
            if !seen.insert(unit.path.as_str()) {
                continue;
            }
            let source = &self.files[&unit.path];
            let anchor = source
                .chunks
                .iter()
                .find(|chunk| chunk.start <= unit.start && unit.start <= chunk.end)
                .or_else(|| source.chunks.first());
            if let Some(chunk) = anchor {
                rank += 1;
                let entry = fused.entry(&chunk.path).or_insert((chunk, 0.0));
                // Keep the family anchor for files present in both channels.
                entry.1 += 1.0 / (60.0 + rank as f64);
            }
        }
        if references {
            let targets = self.referenced_targets(&indexed, query, scope, kind)?;
            for (rank, (i, _)) in targets.into_iter().take(8).enumerate() {
                let unit = &indexed.units[i];
                let source = &self.files[&unit.path];
                if let Some(chunk) = source
                    .chunks
                    .iter()
                    .find(|chunk| chunk.start <= unit.start && unit.start <= chunk.end)
                    .or_else(|| source.chunks.first())
                {
                    let entry = fused.entry(&chunk.path).or_insert((chunk, 0.0));
                    entry.1 += 1.0 / (60.0 + (rank + 1) as f64);
                }
            }
        }
        let mut files: Vec<_> = fused.into_values().collect();
        files.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.path.cmp(&b.0.path)));
        let mut channels: [std::collections::VecDeque<&Chunk>; 3] = Default::default();
        for (chunk, _) in files {
            let slot = match self.files[&chunk.path].kind.as_str() {
                "docs" => 1,
                "test" => 2,
                _ => 0,
            };
            channels[slot].push_back(chunk);
        }
        let mut interleaved = Vec::new();
        while channels.iter().any(|channel| !channel.is_empty()) {
            for channel in &mut channels {
                if let Some(chunk) = channel.pop_front() {
                    interleaved.push((chunk, 1.0 / (interleaved.len() + 1) as f64));
                }
            }
        }
        Ok(interleaved)
    }

    fn referenced_targets(
        &self,
        indexed: &crate::triage::IndexedUnits,
        query: &str,
        scope: Option<&str>,
        kind: Option<&str>,
    ) -> Result<Vec<(usize, f64)>> {
        // Documentation is evidence for explicit names, never executable instructions.
        // Seed scope applies to documents; requested kind applies to delivered targets.
        let docs = self.ranked_family_chunks(query, scope, None)?;
        let mut definitions: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
        for (i, unit) in indexed.units.iter().enumerate() {
            if unit.kind == "function"
                && self.files[&unit.path].kind == "code"
                && let Some(name) = unit.name.as_deref()
            {
                definitions.entry(name).or_default().push(i);
            }
        }
        let mut sources = BTreeMap::new();
        let mut targets: BTreeMap<&str, (usize, f64)> = BTreeMap::new();
        for (seed, (chunk, _)) in docs
            .into_iter()
            .filter(|(chunk, _)| self.files[&chunk.path].kind == "docs")
            .take(8)
            .enumerate()
        {
            if !sources.contains_key(&chunk.path) {
                sources.insert(chunk.path.clone(), self.current_text(&chunk.path)?);
            }
            let text = sources[&chunk.path]
                .lines()
                .skip(chunk.start - 1)
                .take(chunk.end - chunk.start + 1)
                .collect::<Vec<_>>()
                .join("\n");
            for name in crate::references::symbols(&text) {
                let Some(defs) = definitions.get(name.as_str()) else {
                    continue;
                };
                // Count ambiguity globally, before scope/kind filters.
                if defs.len() != 1 {
                    continue;
                }
                let i = defs[0];
                let unit = &indexed.units[i];
                if !scope.is_none_or(|prefix| unit.path.starts_with(prefix))
                    || !kind.is_none_or(|k| self.files[&unit.path].kind == k)
                {
                    continue;
                }
                targets
                    .entry(&unit.path)
                    .or_insert((i, 1.0 / (seed + 1) as f64));
            }
        }
        let mut targets: Vec<_> = targets.into_values().collect();
        targets.sort_by(|a, b| {
            b.1.total_cmp(&a.1)
                .then(indexed.units[a.0].path.cmp(&indexed.units[b.0].path))
        });
        Ok(targets.into_iter().take(8).collect())
    }

    /// Experimental reciprocal-rank fusion of chunk and cached symbol file rankings.
    pub fn evidence_fused_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        ensure_limit(limit)?;
        let interleaved = self.ranked_fused_files(query, scope, kind, policy, false)?;
        let located = self.locate_ranked(
            query,
            budget,
            limit,
            interleaved,
            Policy {
                stop: policy.stop,
                file_score: crate::policy::FileScore::Max,
            },
        )?;
        let mut response = self.evidence_from(located, query, budget, true)?;
        response.experimental =
            "evidence-fused: family/symbol reciprocal file ranks, code/docs/test interleave";
        Ok(response)
    }

    /// Experimental explicit documentation references as a third file-rank channel.
    pub fn evidence_referenced_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        ensure_limit(limit)?;
        let interleaved = self.ranked_fused_files(query, scope, kind, policy, true)?;
        let located = self.locate_ranked(
            query,
            budget,
            limit,
            interleaved,
            Policy {
                stop: policy.stop,
                file_score: crate::policy::FileScore::Max,
            },
        )?;
        let mut response = self.evidence_from(located, query, budget, true)?;
        response.experimental = "evidence-referenced: explicit unique documentation symbols fused with family/unit ranks";
        Ok(response)
    }

    /// Experimental append-only rescue of whole functions explicitly referenced by docs.
    pub fn evidence_rescue_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let mut response = self.evidence_fused_with(query, budget, limit, scope, kind, policy)?;
        response.experimental = "evidence-rescue: fused prefix plus up to four fitting documentation-referenced functions in new files";
        if response.context_tokens >= budget {
            return Ok(response);
        }
        let indexed = crate::triage::indexed_units(self)?;
        let targets = self.referenced_targets(&indexed, query, scope, kind)?;
        let mut delivered: BTreeSet<_> = response.excerpts.iter().map(|e| e.path.clone()).collect();
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let mut added = 0;
        for (i, score) in targets {
            let unit = &indexed.units[i];
            if delivered.contains(&unit.path) {
                continue;
            }
            let source = self.current_text(&unit.path)?;
            let lines: Vec<_> = source.lines().collect();
            if unit.start == 0 || unit.end < unit.start || unit.end > lines.len() {
                continue;
            }
            let physical = lines[unit.start - 1..unit.end].join("\n");
            if physical
                != crate::units::unit_text_for_path(&unit.path, &source, unit.start, unit.end)
            {
                continue;
            }
            let candidate = format!(
                "{}{}\n{physical}\n\n",
                response.context,
                citation(&unit.path, unit.start, unit.end)?
            );
            let tokens = bpe.encode_ordinary(&candidate).len();
            if tokens > budget {
                continue;
            }
            let chunk = self.files[&unit.path]
                .chunks
                .first()
                .context("Referenced file has no chunk")?;
            response.excerpts.push(Excerpt {
                id: format!(
                    "range:{}:{}:{}",
                    handles[chunk.id.as_str()],
                    unit.start,
                    unit.end
                ),
                path: unit.path.clone(),
                start: unit.start,
                end: unit.end,
                source_hash: self.files[&unit.path].hash.clone(),
                score: Some(score),
            });
            response.context = candidate;
            response.context_tokens = tokens;
            delivered.insert(unit.path.clone());
            added += 1;
            if added == 4 {
                break;
            }
        }
        response.matched_files = response.matched_files.max(delivered.len());
        response.status = status(response.matched_files, response.excerpts.len());
        Ok(response)
    }

    /// Experimental append-only local callees of fully delivered functions.
    pub fn evidence_callees_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let mut response = self.evidence_rescue_with(query, budget, limit, scope, kind, policy)?;
        response.experimental =
            "evidence-callees: rescue prefix plus up to four fitting direct local callees";
        if response.context_tokens >= budget || response.excerpts.is_empty() {
            return Ok(response);
        }
        let indexed = crate::triage::indexed_units(self)?;
        let seeds: BTreeSet<_> = indexed
            .units
            .iter()
            .enumerate()
            .filter_map(|(i, unit)| {
                (unit.kind == "function"
                    && response
                        .excerpts
                        .iter()
                        .any(|e| e.path == unit.path && e.start <= unit.start && e.end >= unit.end))
                .then_some(i)
            })
            .collect();
        let mut targets = BTreeSet::new();
        for &(caller, callee) in &indexed.edges {
            let unit = &indexed.units[callee];
            if seeds.contains(&caller)
                && caller != callee
                && unit.kind == "function"
                && indexed.units[caller].path == unit.path
                && scope.is_none_or(|prefix| unit.path.starts_with(prefix))
                && kind.is_none_or(|k| self.files[&unit.path].kind == k)
                && !response
                    .excerpts
                    .iter()
                    .any(|e| e.path == unit.path && e.start <= unit.end && e.end >= unit.start)
            {
                targets.insert(callee);
            }
        }
        let scores = indexed.bm25.score(query);
        let mut targets: Vec<_> = targets.into_iter().collect();
        targets.sort_by(|&a, &b| scores[b].total_cmp(&scores[a]).then(a.cmp(&b)));
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let mut added = 0;
        for i in targets.into_iter().take(32) {
            let unit = &indexed.units[i];
            if response
                .excerpts
                .iter()
                .any(|e| e.path == unit.path && e.start <= unit.end && e.end >= unit.start)
            {
                continue;
            }
            let source = self.current_text(&unit.path)?;
            let lines: Vec<_> = source.lines().collect();
            if unit.start == 0 || unit.end < unit.start || unit.end > lines.len() {
                continue;
            }
            let physical = lines[unit.start - 1..unit.end].join("\n");
            if physical
                != crate::units::unit_text_for_path(&unit.path, &source, unit.start, unit.end)
            {
                continue;
            }
            let candidate = format!(
                "{}{}\n{physical}\n\n",
                response.context,
                citation(&unit.path, unit.start, unit.end)?
            );
            let tokens = bpe.encode_ordinary(&candidate).len();
            if tokens > budget {
                continue;
            }
            let chunk = self.files[&unit.path]
                .chunks
                .first()
                .context("Local callee file has no chunk")?;
            response.excerpts.push(Excerpt {
                id: format!(
                    "range:{}:{}:{}",
                    handles[chunk.id.as_str()],
                    unit.start,
                    unit.end
                ),
                path: unit.path.clone(),
                start: unit.start,
                end: unit.end,
                source_hash: self.files[&unit.path].hash.clone(),
                score: Some(scores[i]),
            });
            response.context = candidate;
            response.context_tokens = tokens;
            added += 1;
            if added == 4 {
                break;
            }
        }
        Ok(response)
    }

    /// Experimental single query-matching local callee with a bounded token allowance.
    pub fn evidence_selective_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let mut response = self.evidence_rescue_with(query, budget, limit, scope, kind, policy)?;
        response.experimental = "evidence-selective: rescue prefix plus one query-matching local callee within a bounded token allowance";
        if response.context_tokens >= budget || response.excerpts.is_empty() {
            return Ok(response);
        }
        let indexed = crate::triage::indexed_units(self)?;
        let seeds: BTreeSet<_> = indexed
            .units
            .iter()
            .enumerate()
            .filter_map(|(i, unit)| {
                (unit.kind == "function"
                    && response
                        .excerpts
                        .iter()
                        .any(|e| e.path == unit.path && e.start <= unit.start && e.end >= unit.end))
                .then_some(i)
            })
            .collect();
        let mut targets = BTreeSet::new();
        for &(caller, callee) in &indexed.edges {
            let unit = &indexed.units[callee];
            if seeds.contains(&caller)
                && caller != callee
                && unit.kind == "function"
                && indexed.units[caller].path == unit.path
                && scope.is_none_or(|prefix| unit.path.starts_with(prefix))
                && kind.is_none_or(|k| self.files[&unit.path].kind == k)
                && !response
                    .excerpts
                    .iter()
                    .any(|e| e.path == unit.path && e.start <= unit.end && e.end >= unit.start)
            {
                targets.insert(callee);
            }
        }
        let scores = indexed.bm25.score(query);
        let mut targets: Vec<_> = targets.into_iter().filter(|&i| scores[i] > 0.0).collect();
        targets.sort_by(|&a, &b| scores[b].total_cmp(&scores[a]).then(a.cmp(&b)));
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let prefix_tokens = response.context_tokens;
        let allowance = (budget / 10).min(256);
        for i in targets.into_iter().take(32) {
            let unit = &indexed.units[i];
            if response
                .excerpts
                .iter()
                .any(|e| e.path == unit.path && e.start <= unit.end && e.end >= unit.start)
            {
                continue;
            }
            let source = self.current_text(&unit.path)?;
            let lines: Vec<_> = source.lines().collect();
            if unit.start == 0 || unit.end < unit.start || unit.end > lines.len() {
                continue;
            }
            let physical = lines[unit.start - 1..unit.end].join("\n");
            if physical
                != crate::units::unit_text_for_path(&unit.path, &source, unit.start, unit.end)
            {
                continue;
            }
            let candidate = format!(
                "{}{}\n{physical}\n\n",
                response.context,
                citation(&unit.path, unit.start, unit.end)?
            );
            let tokens = bpe.encode_ordinary(&candidate).len();
            if tokens > budget || tokens.saturating_sub(prefix_tokens) > allowance {
                continue;
            }
            let chunk = self.files[&unit.path]
                .chunks
                .first()
                .context("Local callee file has no chunk")?;
            response.excerpts.push(Excerpt {
                id: format!(
                    "range:{}:{}:{}",
                    handles[chunk.id.as_str()],
                    unit.start,
                    unit.end
                ),
                path: unit.path.clone(),
                start: unit.start,
                end: unit.end,
                source_hash: self.files[&unit.path].hash.clone(),
                score: Some(scores[i]),
            });
            response.context = candidate;
            response.context_tokens = tokens;
            break;
        }
        Ok(response)
    }

    /// Experimental round-robin code/docs/test file delivery, retaining within-kind ranks.
    pub fn evidence_stratified_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        ensure_limit(limit)?;
        let ranking = self.ranked_family_chunks(query, scope, kind)?;
        let files = policy.file_score.files(ranking);
        let mut channels: [std::collections::VecDeque<&Chunk>; 3] = Default::default();
        for (chunk, _) in files {
            let slot = match self.files[&chunk.path].kind.as_str() {
                "docs" => 1,
                "test" => 2,
                _ => 0,
            };
            channels[slot].push_back(chunk);
        }
        let mut stratified = Vec::new();
        while channels.iter().any(|channel| !channel.is_empty()) {
            for channel in &mut channels {
                if let Some(chunk) = channel.pop_front() {
                    let score = 1.0 / (stratified.len() + 1) as f64;
                    stratified.push((chunk, score));
                }
            }
        }
        let located = self.locate_ranked(
            query,
            budget,
            limit,
            stratified,
            Policy {
                stop: policy.stop,
                file_score: crate::policy::FileScore::Max,
            },
        )?;
        let mut response = self.evidence_from(located, query, budget, true)?;
        response.experimental =
            "evidence-stratified: code/docs/test interleave within family relevance ranks";
        Ok(response)
    }

    /// Experimental full-file passage selection within the unchanged family-ranked files.
    pub fn evidence_passages_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        ensure_limit(limit)?;
        let ranking = self.ranked_family_chunks(query, scope, kind)?;
        let located = self.locate_ranked(query, budget, limit, ranking, policy)?;
        let n = self.lengths.len() as f64;
        let weights: BTreeMap<_, _> = self
            .analyzer
            .terms(query)
            .into_iter()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .filter_map(|term| {
                self.postings.get(&term).map(|hits| {
                    let df = hits.len() as f64;
                    (term, (1.0 + (n - df + 0.5) / (df + 0.5)).ln())
                })
            })
            .collect();
        let bpe = cl100k_base()?;
        let share = budget / located.hints.len().max(1);
        let mut context = String::new();
        let mut excerpts = Vec::new();
        for hint in located.hints {
            let source = self.current_text(&hint.path)?;
            let lines: Vec<_> = source.lines().collect();
            let Some((mut start, mut end, anchor)) =
                crate::passages::best_passage(&lines, &weights, &self.analyzer)
            else {
                continue;
            };
            while start < end {
                let section = format!(
                    "{}\n{}\n\n",
                    citation(&hint.path, start + 1, end)?,
                    lines[start..end].join("\n")
                );
                if bpe.encode_ordinary(&section).len() <= share {
                    let candidate = format!("{context}{section}");
                    if bpe.encode_ordinary(&candidate).len() <= budget {
                        context = candidate;
                        excerpts.push(Excerpt {
                            id: format!("range:{}:{}:{end}", hint.id, start + 1),
                            path: hint.path,
                            start: start + 1,
                            end,
                            source_hash: hint.source_hash,
                            score: Some(hint.score),
                        });
                    }
                    break;
                }
                if anchor - start > end - 1 - anchor {
                    start += 1;
                } else {
                    end -= 1;
                }
            }
        }
        Ok(EvidenceResponse {
            status: status(located.matched_files, excerpts.len()),
            output_kind: "source_excerpts",
            tokenizer: TOKENIZER,
            budget_scope: SEARCH_SCOPE,
            budget,
            context_tokens: bpe.encode_ordinary(&context).len(),
            matched_files: located.matched_files,
            excerpts,
            context,
            experimental: "evidence-passages: full-file lexical passages within family-ranked files",
        })
    }

    /// Experimental append-only passages for query terms missing from structural evidence.
    pub fn evidence_complement_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let mut response =
            self.evidence_families_with(query, budget, limit, scope, kind, policy)?;
        response.experimental =
            "evidence-complement: structural prefix plus non-overlapping novel-term passages";
        let count = response.excerpts.len();
        let share = (budget - response.context_tokens) / count.max(1);
        if share == 0 {
            return Ok(response);
        }
        let terms: BTreeSet<_> = self.analyzer.terms(query).into_iter().collect();
        let n = self.lengths.len() as f64;
        let bpe = cl100k_base()?;
        for i in 0..count {
            let base = &response.excerpts[i];
            let source = self.current_text(&base.path)?;
            let lines: Vec<_> = source.lines().collect();
            let covered: BTreeSet<_> = lines[base.start - 1..base.end]
                .iter()
                .flat_map(|line| self.analyzer.terms(line))
                .collect();
            let weights: BTreeMap<_, _> = terms
                .difference(&covered)
                .filter_map(|term| {
                    self.postings.get(term).map(|hits| {
                        let df = hits.len() as f64;
                        (term.clone(), (1.0 + (n - df + 0.5) / (df + 0.5)).ln())
                    })
                })
                .collect();
            let Some((mut start, mut end, anchor)) = crate::passages::best_complement(
                &lines,
                &weights,
                (base.start - 1, base.end),
                &self.analyzer,
            ) else {
                continue;
            };
            while start < end {
                let body = lines[start..end].join("\n");
                let has_novel = self
                    .analyzer
                    .terms(&body)
                    .iter()
                    .any(|term| weights.contains_key(term));
                let section = format!("{}\n{body}\n\n", citation(&base.path, start + 1, end)?);
                if has_novel && bpe.encode_ordinary(&section).len() <= share {
                    let context = format!("{}{section}", response.context);
                    if bpe.encode_ordinary(&context).len() <= budget {
                        let handle = base.id.rsplitn(3, ':').last().expect("range prefix");
                        let excerpt = Excerpt {
                            id: format!("{handle}:{}:{end}", start + 1),
                            path: base.path.clone(),
                            start: start + 1,
                            end,
                            source_hash: base.source_hash.clone(),
                            score: base.score,
                        };
                        response.context = context;
                        response.excerpts.push(excerpt);
                    }
                    break;
                }
                if anchor - start > end - 1 - anchor {
                    start += 1;
                } else {
                    end -= 1;
                }
            }
        }
        response.context_tokens = bpe.encode_ordinary(&response.context).len();
        Ok(response)
    }

    /// Experimental append-only recovery through one-hop cross-file calls.
    pub fn evidence_linked_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let response = self.evidence_units_with(query, budget, limit, scope, kind, policy)?;
        self.linked_evidence_from(response, query, budget, scope, kind, false)
    }

    /// Experimental atomic linked-function bundles, including direct local helpers.
    pub fn evidence_bundles_with(
        &self,
        query: &str,
        budget: usize,
        limit: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        policy: Policy,
    ) -> Result<EvidenceResponse> {
        let response = self.evidence_units_with(query, budget, limit, scope, kind, policy)?;
        self.linked_evidence_from(response, query, budget, scope, kind, true)
    }

    fn linked_evidence_from(
        &self,
        mut response: EvidenceResponse,
        query: &str,
        budget: usize,
        scope: Option<&str>,
        kind: Option<&str>,
        bundles: bool,
    ) -> Result<EvidenceResponse> {
        response.experimental = if bundles {
            "evidence-bundles: structural evidence plus up to four atomic linked-function/helper bundles"
        } else {
            "evidence-linked: structural evidence plus up to four fitting one-hop linked files"
        };
        if response.excerpts.is_empty() || response.context_tokens >= budget {
            return Ok(response);
        }
        let indexed = crate::triage::indexed_units(self)?;
        let seeds: BTreeSet<_> = response.excerpts.iter().map(|e| e.path.as_str()).collect();
        let mut neighbours = BTreeSet::new();
        for &(a, b) in &indexed.edges {
            let (a, b) = (&indexed.units[a].path, &indexed.units[b].path);
            if a != b {
                if seeds.contains(a.as_str()) {
                    neighbours.insert(b.as_str());
                }
                if seeds.contains(b.as_str()) {
                    neighbours.insert(a.as_str());
                }
            }
        }
        let body = crate::units::unit_scores(&indexed.bm25, &indexed.edges, query);
        let scores = crate::units::symbol_scores(&indexed.units, &body, query);
        let mut order: Vec<_> = (0..scores.len())
            .filter(|&i| {
                let path = indexed.units[i].path.as_str();
                scores[i] > 0.0
                    && neighbours.contains(path)
                    && !seeds.contains(path)
                    && scope.is_none_or(|prefix| path.starts_with(prefix))
                    && kind.is_none_or(|kind| self.files[path].kind == kind)
            })
            .collect();
        order.sort_by(|&a, &b| scores[b].total_cmp(&scores[a]).then(a.cmp(&b)));
        let handles = compact_handles(self.chunks().map(|c| c.id.as_str()));
        let bpe = cl100k_base()?;
        let mut added = BTreeSet::new();
        for i in order {
            let unit = &indexed.units[i];
            if added.contains(&unit.path) {
                continue;
            }
            let source = self.current_text(&unit.path)?;
            let lines: Vec<_> = source.lines().collect();
            let mut bundle = vec![i];
            if bundles && unit.kind == "function" {
                let mut helpers: Vec<_> = indexed
                    .edges
                    .iter()
                    .filter_map(|&(caller, callee)| {
                        let helper = &indexed.units[callee];
                        (caller == i
                            && callee != i
                            && helper.path == unit.path
                            && helper.kind == "function"
                            && !(unit.start <= helper.start && unit.end >= helper.end))
                            .then_some(callee)
                    })
                    .collect();
                helpers.sort_by(|&a, &b| body[b].total_cmp(&body[a]).then(a.cmp(&b)));
                helpers.dedup();
                bundle.extend(helpers.into_iter().take(2));
            }
            let mut candidate = response.context.clone();
            let mut valid = true;
            for &j in &bundle {
                let member = &indexed.units[j];
                if member.start == 0 || member.end < member.start || member.end > lines.len() {
                    valid = false;
                    break;
                }
                let physical = lines[member.start - 1..member.end].join("\n");
                if physical
                    != crate::units::unit_text_for_path(
                        &member.path,
                        &source,
                        member.start,
                        member.end,
                    )
                {
                    valid = false;
                    break;
                }
                candidate.push_str(&format!(
                    "{}\n{physical}\n\n",
                    citation(&member.path, member.start, member.end)?
                ));
            }
            if !valid {
                continue;
            }
            let tokens = bpe.encode_ordinary(&candidate).len();
            if tokens > budget {
                continue;
            }
            let chunk = self.files[&unit.path]
                .chunks
                .first()
                .context("Linked file has no chunk")?;
            for j in bundle {
                let member = &indexed.units[j];
                response.excerpts.push(Excerpt {
                    id: format!(
                        "range:{}:{}:{}",
                        handles[chunk.id.as_str()],
                        member.start,
                        member.end
                    ),
                    path: member.path.clone(),
                    start: member.start,
                    end: member.end,
                    source_hash: self.files[&member.path].hash.clone(),
                    score: Some(scores[j]),
                });
            }
            response.context = candidate;
            response.context_tokens = tokens;
            added.insert(unit.path.clone());
            if added.len() == 4 {
                break;
            }
        }
        Ok(response)
    }

    fn evidence_from(
        &self,
        located: LocateResponse,
        query: &str,
        budget: usize,
        structural: bool,
    ) -> Result<EvidenceResponse> {
        let indexed = if structural && !located.hints.is_empty() {
            Some(crate::triage::indexed_units(self)?)
        } else {
            None
        };
        let mut ranked = HashMap::<&str, Vec<usize>>::new();
        if let Some(indexed) = &indexed {
            let body = crate::units::unit_scores(&indexed.bm25, &indexed.edges, query);
            let scores = crate::units::symbol_scores(&indexed.units, &body, query);
            let mut order: Vec<_> = (0..scores.len()).filter(|&i| scores[i] > 0.0).collect();
            order.sort_by(|&a, &b| scores[b].total_cmp(&scores[a]).then(a.cmp(&b)));
            for i in order {
                ranked
                    .entry(indexed.units[i].path.as_str())
                    .or_default()
                    .push(i);
            }
        }
        let bpe = cl100k_base()?;
        let share = budget / located.hints.len().max(1);
        let mut context = String::new();
        let mut excerpts = Vec::new();
        for hint in located.hints {
            let source = self.current_text(&hint.path)?;
            let lines: Vec<_> = source.lines().collect();
            if lines.is_empty() {
                continue;
            }
            let anchor = hint.line.saturating_sub(1).min(lines.len() - 1);
            let mut start = anchor.saturating_sub(8);
            let mut end = (start + 24).min(lines.len());
            if let Some(indexed) = &indexed {
                for &i in ranked.get(hint.path.as_str()).into_iter().flatten() {
                    let unit = &indexed.units[i];
                    if unit.start == 0 || unit.end < unit.start || unit.end > lines.len() {
                        continue;
                    }
                    let physical = lines[unit.start - 1..unit.end].join("\n");
                    // Legacy units use Python line splitting. Never relabel that
                    // payload with physical source coordinates when they differ.
                    if physical
                        != crate::units::unit_text_for_path(
                            &hint.path, &source, unit.start, unit.end,
                        )
                    {
                        continue;
                    }
                    let section = format!(
                        "{}\n{physical}\n\n",
                        citation(&hint.path, unit.start, unit.end)?,
                    );
                    if bpe.encode_ordinary(&section).len() <= share {
                        start = unit.start - 1;
                        end = unit.end;
                        break;
                    }
                }
            }
            while start < end {
                let section = format!(
                    "{}\n{}\n\n",
                    citation(&hint.path, start + 1, end)?,
                    lines[start..end].join("\n")
                );
                if bpe.encode_ordinary(&section).len() <= share {
                    let candidate = format!("{context}{section}");
                    if bpe.encode_ordinary(&candidate).len() <= budget {
                        context = candidate;
                        excerpts.push(Excerpt {
                            id: format!("range:{}:{}:{end}", hint.id, start + 1),
                            path: hint.path,
                            start: start + 1,
                            end,
                            source_hash: hint.source_hash,
                            score: Some(hint.score),
                        });
                    }
                    break;
                }
                if anchor - start > end - 1 - anchor {
                    start += 1;
                } else {
                    end -= 1;
                }
            }
        }
        Ok(EvidenceResponse {
            status: status(located.matched_files, excerpts.len()),
            output_kind: "source_excerpts",
            tokenizer: TOKENIZER,
            budget_scope: SEARCH_SCOPE,
            budget,
            context_tokens: bpe.encode_ordinary(&context).len(),
            matched_files: located.matched_files,
            excerpts,
            context,
            experimental: if structural {
                "evidence-units: locate-ranked files with complete fitting units or source-window fallback"
            } else {
                "evidence: locate-ranked files with equal source-window shares"
            },
        })
    }

    pub fn expand(&self, id: &str, before: usize, after: usize, budget: usize) -> Result<Response> {
        // Evidence may select lines outside the locate chunk. Keep its snapshot
        // anchor for file resolution, but expand the actual delivered range.
        let (anchor_id, range) = if let Some(value) = id.strip_prefix("range:") {
            ensure!(value.len() <= 106, "Invalid evidence range ID");
            let parts: Vec<_> = value.split(':').collect();
            ensure!(parts.len() == 3, "Invalid evidence range ID");
            let parse_line = |s: &str| -> Result<usize> {
                ensure!(
                    !s.starts_with('0') && s.bytes().all(|c| c.is_ascii_digit()),
                    "Invalid evidence range line"
                );
                s.parse().context("Invalid evidence range line")
            };
            let start = parse_line(parts[1])?;
            let end = parse_line(parts[2])?;
            ensure!(start <= end, "Invalid evidence range order");
            (parts[0], Some((start, end)))
        } else {
            (id, None)
        };
        ensure!(
            (MIN_HANDLE..=64).contains(&anchor_id.len())
                && anchor_id
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
            "Excerpt ID must be 12 to 64 lowercase hex characters"
        );
        let mut matches = self.chunks().filter(|c| c.id.starts_with(anchor_id));
        let chunk = matches
            .next()
            .context("Unknown excerpt ID; search again after indexing")?;
        ensure!(
            matches.next().is_none(),
            "Ambiguous excerpt ID; use a longer handle"
        );
        let text = self.current_text(&chunk.path)?;
        let lines: Vec<&str> = text.lines().collect();
        let (base_start, base_end) = range.unwrap_or((chunk.start, chunk.end));
        ensure!(
            base_end <= lines.len(),
            "Evidence range exceeds source lines"
        );
        let start = base_start.saturating_sub(before).max(1);
        let end = base_end.saturating_add(after).min(lines.len());
        let section = format!(
            "{}\n{}",
            citation(&chunk.path, start, end)?,
            lines[start - 1..end].join("\n")
        );
        let bpe = cl100k_base()?;
        let mut packer = Packer::new(&bpe, budget);
        let excerpts = if packer.try_push(&section) {
            let mut excerpt = self.excerpt(chunk, start, end, None);
            if range.is_some() {
                excerpt.id = id.to_string();
            }
            vec![excerpt]
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

#[cfg(test)]
mod family_tests {
    use super::*;
    use std::fs;

    #[test]
    fn families_are_bounded_and_safe_for_arbitrary_query_text() {
        assert_eq!(family_distance("decorator", "decorate"), Some(2));
        assert_eq!(family_distance("created", "create"), Some(1));
        assert_eq!(family_distance("caches", "cache"), Some(1));
        for (a, b) in [
            ("map", "maps"),
            ("caféword", "cafeword"),
            ("12345", "12346"),
            ("alpha", "alphiab"),
            ("calculate", "xalculate"),
            ("calculate", "calculationextra"),
        ] {
            assert_eq!(family_distance(a, b), None, "{a} / {b}");
        }
        assert_eq!(family_distance(&"a".repeat(65), &"a".repeat(65)), None);
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("a.rs"),
            "// calculate calculata calculatb calculatc calculatd calculatf\n",
        )
        .unwrap();
        let root = crate::canonical_root(dir.path()).unwrap();
        Index::build(&root, false).unwrap();
        let index = Index::load(&root).unwrap();
        assert_eq!(
            index.term_family("calculate"),
            vec![
                ("calculate".into(), 0),
                ("calculata".into(), 1),
                ("calculatb".into(), 1),
                ("calculatc".into(), 1),
                ("calculatd".into(), 1),
            ]
        );
    }

    #[test]
    fn additional_aliases_cannot_inflate_an_already_exact_group() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a.rs"), "// calculate\n").unwrap();
        let root = crate::canonical_root(dir.path()).unwrap();
        Index::build(&root, false).unwrap();
        let mut index = Index::load(&root).unwrap();
        let before: Vec<_> = index
            .ranked_family_chunks("calculate", None, None)
            .unwrap()
            .iter()
            .map(|(c, s)| (c.path.clone(), *s))
            .collect();
        let postings = index.postings["calculate"].clone();
        index.postings.insert("calculata".into(), postings.clone());
        index.postings.insert("calculatb".into(), postings);
        let after: Vec<_> = index
            .ranked_family_chunks("calculate", None, None)
            .unwrap()
            .iter()
            .map(|(c, s)| (c.path.clone(), *s))
            .collect();
        assert_eq!(before, after);
    }
}
