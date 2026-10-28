//! Experimental agent orientation: `triage` (where to work) and `outline` (what a file holds).
//!
//! `triage` composes two screened arms in one answer: files ranked by `locate`
//! file scores (damped, floor stop, rewritten query) raised over cross-file call
//! edges (`file_graph_ids`, preregistration v24), then whole functions from `units`
//! with the rewritten query (`ast_graph_ids`). `outline` lists a file's function units so an agent can read
//! one function instead of the whole file. Neither is an accepted improvement.
use crate::rewrite::rewrite_query;
use crate::source::digest;
use crate::units::{
    Bm25, FLOOR, Unit, WEIGHT, append_units_within_budget, call_edges, code_units, file_propagate,
    linked_file_candidates, pack_units, pack_units_file_diverse, pack_units_focused,
    packing_candidates, retained_body_candidates, symbol_scores, term_counts, unit_scores,
    unit_text_for_path,
};
use crate::{FileScore, Index, Policy, Stop};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use tiktoken_rs::CoreBPE;

/// Changes whenever unit extraction could change; a mismatching cache is rebuilt.
const UNIT_CACHE_VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), "/units-5-rust");

#[derive(Default, Serialize, Deserialize)]
struct UnitCache {
    version: String,
    files: BTreeMap<String, CachedUnits>,
}

#[derive(Serialize, Deserialize)]
struct CachedUnits {
    hash: String,
    /// Units without their text, which is read from the source file when a unit is delivered.
    units: Vec<Unit>,
    /// Each unit's sorted term counts, so queries never re-tokenize unit text.
    counts: Vec<Vec<(String, u32)>>,
}

/// What a query needs, derived from the per-file cache and stored in
/// `.graf/unit-index.bin`: every unit in path order (without text or calls), BM25
/// postings over them, and their call edges. Valid while every source hash matches.
#[derive(Serialize, Deserialize)]
pub struct IndexedUnits {
    version: String,
    /// Digest of every indexed path and source hash.
    sources: String,
    pub units: Vec<Unit>,
    pub bm25: Bm25,
    pub edges: Vec<(usize, usize)>,
}

fn sources_digest(index: &Index) -> String {
    let listing: String = index
        .files
        .iter()
        .map(|(path, source)| format!("{path}\0{}\n", source.hash))
        .collect();
    digest(&listing)
}

/// Files listed before the code.
pub const TRIAGE_FILES: usize = 8;
/// The locate budget the file-ranking screen used to collect file scores.
const LOCATE_BUDGET: usize = 2000;
/// Characters of each signature line in an outline.
const SIGNATURE_CHARS: usize = 120;

fn outline_signature(path: &str, name: Option<&str>, first_line: &str) -> String {
    let signature = if path.ends_with(".rs") {
        name.map_or_else(
            || first_line.to_string(),
            |name| format!("{name}: {first_line}"),
        )
    } else {
        first_line.to_string()
    };
    signature.chars().take(SIGNATURE_CHARS).collect()
}

#[derive(Debug, Serialize)]
pub struct CitedUnit {
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub name: Option<String>,
}

impl From<&Unit> for CitedUnit {
    fn from(unit: &Unit) -> Self {
        Self {
            path: unit.path.clone(),
            start: unit.start,
            end: unit.end,
            name: unit.name.clone(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct Triage {
    pub query: String,
    pub files: Vec<String>,
    pub units: Vec<CitedUnit>,
    pub budget: usize,
    pub context_tokens: usize,
    pub context: String,
    pub experimental: &'static str,
}

/// Every indexed file's units, in path order. A current `.graf/unit-index.bin` is loaded
/// as is; otherwise units come from `.graf/units.bin`, cached by source hash so only new
/// or changed files are parsed, and the query index is rebuilt from them.
pub fn indexed_units(index: &Index) -> Result<IndexedUnits> {
    let sources = sources_digest(index);
    let query_index = index.root.join(".graf/unit-index.bin");
    if let Some(indexed) = fs::read(&query_index)
        .ok()
        .and_then(|bytes| bincode::deserialize::<IndexedUnits>(&bytes).ok())
        .filter(|i| i.version == UNIT_CACHE_VERSION && i.sources == sources)
    {
        return Ok(indexed);
    }
    let path = index.root.join(".graf/units.bin");
    let mut cache: UnitCache = fs::read(&path)
        .ok()
        .and_then(|bytes| bincode::deserialize(&bytes).ok())
        .filter(|c: &UnitCache| c.version == UNIT_CACHE_VERSION)
        .unwrap_or_default();
    let mut fresh = BTreeMap::new();
    let mut stale = Vec::new();
    for (file, source) in &index.files {
        match cache.files.remove(file) {
            Some(cached) if cached.hash == source.hash => {
                fresh.insert(file.clone(), cached);
            }
            _ => stale.push(file.as_str()),
        }
    }
    let changed = !stale.is_empty();
    fresh.extend(parse_files(index, &stale)?);
    let bm25 = Bm25::from_counts(
        fresh
            .values()
            .flat_map(|c| c.counts.iter().map(Vec::as_slice)),
    );
    let mut units: Vec<Unit> = fresh
        .values()
        .flat_map(|c| c.units.iter().cloned())
        .collect();
    let edges = call_edges(&units);
    for unit in &mut units {
        unit.calls.clear(); // Encoded in `edges`.
    }
    if changed || !cache.files.is_empty() {
        save_cache(
            index,
            "units.bin",
            &UnitCache {
                version: UNIT_CACHE_VERSION.into(),
                files: fresh,
            },
        )?;
    }
    let indexed = IndexedUnits {
        version: UNIT_CACHE_VERSION.into(),
        sources,
        units,
        bm25,
        edges,
    };
    save_cache(index, "unit-index.bin", &indexed)?;
    Ok(indexed)
}

/// Units and term counts of `files`, parsed on all available cores.
fn parse_files(index: &Index, files: &[&str]) -> Result<Vec<(String, CachedUnits)>> {
    let threads = std::thread::available_parallelism().map_or(1, |n| n.get());
    let size = files.len().div_ceil(threads).max(1);
    std::thread::scope(|scope| {
        let workers: Vec<_> = files
            .chunks(size)
            .map(|chunk| {
                scope.spawn(move || {
                    chunk
                        .iter()
                        .map(|file| parse_file(index, file))
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        workers
            .into_iter()
            .flat_map(|worker| worker.join().expect("unit parser panicked"))
            .collect()
    })
}

fn parse_file(index: &Index, file: &str) -> Result<(String, CachedUnits)> {
    let text = index.current_text(file)?;
    let mut units = code_units(file, &text);
    let counts = units.iter().map(|unit| term_counts(&unit.text)).collect();
    for unit in &mut units {
        unit.text.clear();
    }
    let hash = index.files[file].hash.clone();
    Ok((
        file.to_string(),
        CachedUnits {
            hash,
            units,
            counts,
        },
    ))
}

fn save_cache(index: &Index, name: &str, cache: &impl Serialize) -> Result<()> {
    let dir = index.root.join(".graf");
    ensure!(
        !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
        ".graf must not be a symlink"
    );
    let mut temp = tempfile::NamedTempFile::new_in(&dir)?;
    temp.write_all(&bincode::serialize(cache)?)?;
    crate::index::replace(temp.into_temp_path(), &dir.join(name))
        .context("Cannot save the unit cache")?;
    Ok(())
}

/// Ranked whole units for `query`; the files of the delivered units are checked against
/// the snapshot, as search and expand check theirs.
pub fn ranked_units(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    ranked_from(bpe, index, &indexed_units(index)?, query, budget)
}

/// Experimental query-centered excerpts for oversized top-ranked units.
pub fn ranked_units_focused(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(
        bpe,
        index,
        &indexed_units(index)?,
        query,
        budget,
        true,
        UnitRanking::Body,
    )
}

/// Experimental name/path ranking; optionally retain query-centered truncation.
pub fn ranked_units_symbols(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
    focus: bool,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(
        bpe,
        index,
        &indexed_units(index)?,
        query,
        budget,
        focus,
        UnitRanking::Symbols,
    )
}

/// [`ranked_units`] over an already loaded function index.
pub fn ranked_from(
    bpe: &CoreBPE,
    index: &Index,
    indexed: &IndexedUnits,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(bpe, index, indexed, query, budget, false, UnitRanking::Body)
}

/// Experimental symbol ranking with the union of body and symbol eligibility.
pub fn ranked_units_retaining_body(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
    focus: bool,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(
        bpe,
        index,
        &indexed_units(index)?,
        query,
        budget,
        focus,
        UnitRanking::RetainBody,
    )
}

/// Experimental file-diverse packing of retained body/symbol candidates.
pub fn ranked_units_file_diverse(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(
        bpe,
        index,
        &indexed_units(index)?,
        query,
        budget,
        true,
        UnitRanking::RetainBodyDiverse,
    )
}

/// Experimental same-file direct callee evidence after each first file seed.
pub fn ranked_units_linked(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(
        bpe,
        index,
        &indexed_units(index)?,
        query,
        budget,
        true,
        UnitRanking::Linked,
    )
}

/// Experimental callee evidence appended only in unused v42 output space.
pub fn ranked_units_linked_tail(
    bpe: &CoreBPE,
    index: &Index,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    ranked_with_options(
        bpe,
        index,
        &indexed_units(index)?,
        query,
        budget,
        true,
        UnitRanking::LinkedTail,
    )
}

#[derive(Clone, Copy)]
enum UnitRanking {
    Body,
    Symbols,
    RetainBody,
    RetainBodyDiverse,
    Linked,
    LinkedTail,
}

fn ranked_with_options(
    bpe: &CoreBPE,
    index: &Index,
    indexed: &IndexedUnits,
    query: &str,
    budget: usize,
    focus: bool,
    ranking: UnitRanking,
) -> Result<(Vec<Unit>, String)> {
    let body = unit_scores(&indexed.bm25, &indexed.edges, query);
    let scores = match ranking {
        UnitRanking::Body => std::borrow::Cow::Borrowed(body.as_slice()),
        _ => std::borrow::Cow::Owned(symbol_scores(&indexed.units, &body, query)),
    };
    let candidates = match ranking {
        UnitRanking::RetainBody
        | UnitRanking::RetainBodyDiverse
        | UnitRanking::Linked
        | UnitRanking::LinkedTail => retained_body_candidates(&body, &scores),
        _ => packing_candidates(&scores),
    };
    let candidates = if matches!(ranking, UnitRanking::Linked) {
        linked_file_candidates(&indexed.units, &indexed.edges, &body, &scores, &candidates)
    } else {
        candidates
    };
    // RetainBody already applied each signal's floor before taking the union.
    let floor = if matches!(
        ranking,
        UnitRanking::RetainBody
            | UnitRanking::RetainBodyDiverse
            | UnitRanking::Linked
            | UnitRanking::LinkedTail
    ) {
        0.0
    } else {
        FLOOR
    };
    // Packing only reaches units at or above the floor; they keep their relative order,
    // so packing this subset is identical to packing every unit.
    let mut units = Vec::new();
    let mut kept = Vec::new();
    let mut texts: BTreeMap<&str, String> = BTreeMap::new();
    for i in candidates {
        let unit = &indexed.units[i];
        if !texts.contains_key(unit.path.as_str()) {
            texts.insert(&unit.path, index.current_text(&unit.path)?);
        }
        units.push(Unit {
            text: unit_text_for_path(&unit.path, &texts[unit.path.as_str()], unit.start, unit.end),
            ..unit.clone()
        });
        kept.push(if matches!(ranking, UnitRanking::Linked) {
            (indexed.units.len() - kept.len()) as f64
        } else {
            scores[i]
        });
    }
    let packed = if matches!(
        ranking,
        UnitRanking::RetainBodyDiverse | UnitRanking::LinkedTail
    ) {
        pack_units_file_diverse(bpe, &units, &kept, query, budget)
    } else if focus {
        pack_units_focused(bpe, &units, &kept, query, budget, floor)
    } else {
        pack_units(bpe, &units, &kept, budget, floor)
    };
    if !matches!(ranking, UnitRanking::LinkedTail) {
        return Ok(packed);
    }
    // The baseline prefix is already fixed. Only full first-per-file seeds can
    // contribute same-file, one-hop helpers, preserving v43's two-helper quota.
    let mut seen_files = std::collections::BTreeSet::new();
    let mut helpers = Vec::new();
    for seed in &packed.0 {
        if !seen_files.insert(seed.path.as_str()) {
            continue;
        }
        let Some(caller) = indexed
            .units
            .iter()
            .position(|u| u.path == seed.path && u.start == seed.start && u.end == seed.end)
        else {
            continue;
        };
        let mut targets: Vec<_> = indexed
            .edges
            .iter()
            .filter_map(|&(a, b)| {
                (a == caller && a != b && indexed.units[b].path == seed.path && body[b] > 0.0)
                    .then_some(b)
            })
            .collect();
        targets.sort_by(|&a, &b| body[b].total_cmp(&body[a]).then(a.cmp(&b)));
        targets.dedup();
        for target in targets.into_iter().take(2) {
            let unit = &indexed.units[target];
            if !texts.contains_key(unit.path.as_str()) {
                texts.insert(&unit.path, index.current_text(&unit.path)?);
            }
            helpers.push(Unit {
                text: unit_text_for_path(
                    &unit.path,
                    &texts[unit.path.as_str()],
                    unit.start,
                    unit.end,
                ),
                ..unit.clone()
            });
        }
    }
    Ok(append_units_within_budget(
        bpe, packed.0, packed.1, &helpers, budget,
    ))
}

/// Likely files, then whole likely functions, within `budget` cl100k tokens in total.
pub fn triage(bpe: &CoreBPE, index: &Index, issue: &str, budget: usize) -> Result<Triage> {
    triage_with(bpe, index, &indexed_units(index)?, issue, budget)
}

/// [`triage`] over an already loaded function index.
pub fn triage_with(
    bpe: &CoreBPE,
    index: &Index,
    indexed: &IndexedUnits,
    issue: &str,
    budget: usize,
) -> Result<Triage> {
    let query = rewrite_query(issue);
    let policy = Policy {
        stop: Stop::Floor,
        file_score: FileScore::Damped,
    };
    // The screened file_graph_ids arm: damped, floor-stopped locate scores for the
    // rewritten query, raised over cross-file call edges.
    let located = index.locate_with(&query, LOCATE_BUDGET, 1000, None, None, policy)?;
    let scores: BTreeMap<String, f64> = located
        .hints
        .iter()
        .map(|h| (h.path.clone(), h.score))
        .collect();
    let mut files: Vec<String> = file_propagate(&scores, &indexed.units, &indexed.edges, WEIGHT)
        .into_iter()
        .take(TRIAGE_FILES)
        .collect();
    let mut context = String::new();
    if !files.is_empty() {
        context.push_str("Likely files:\n");
        for path in &files {
            context.push_str(&format!("- {path}\n"));
        }
        context.push('\n');
    }
    if bpe.encode_ordinary(&context).len() > budget {
        context.clear();
        files.clear();
    }
    let header = "Likely code:\n";
    let remaining = budget
        .saturating_sub(bpe.encode_ordinary(&context).len())
        .saturating_sub(bpe.encode_ordinary(header).len());
    let mut cited = Vec::new();
    if remaining > 0 {
        let (picked, code) = ranked_from(bpe, index, indexed, &query, remaining)?;
        if !code.is_empty() {
            context.push_str(header);
            context.push_str(&code);
            cited = picked.iter().map(CitedUnit::from).collect();
        }
    }
    Ok(Triage {
        query,
        files,
        units: cited,
        budget,
        context_tokens: bpe.encode_ordinary(&context).len(),
        context,
        experimental: "triage: file_graph_ids files, then ast_graph_ids units",
    })
}

#[derive(Debug, Serialize)]
pub struct OutlineEntry {
    pub start: usize,
    pub end: usize,
    pub name: Option<String>,
    pub signature: String,
}

#[derive(Debug, Serialize)]
pub struct Outline {
    pub path: String,
    pub lines: usize,
    pub functions: Vec<OutlineEntry>,
    pub context: String,
}

/// A budgeted page of an outline. `next_entry` is the zero-based function offset to
/// pass to the next call; it stays at the first omitted entry when that entry cannot
/// fit in the current budget. `truncated` is true whenever entries remain.
#[derive(Debug, Serialize)]
pub struct BudgetedOutline {
    pub path: String,
    pub lines: usize,
    pub functions: Vec<OutlineEntry>,
    pub context: String,
    pub budget: usize,
    pub context_tokens: usize,
    pub offset: usize,
    pub limit: usize,
    pub next_entry: Option<usize>,
    pub next_offset: Option<usize>,
    pub truncated: bool,
    pub total_functions: usize,
}

/// The functions of an indexed source file, each with its line range and first line.
pub fn outline(index: &Index, path: &str) -> Result<Outline> {
    let text = index.current_text(path)?;
    let units = code_units(path, &text);
    ensure!(
        units.iter().all(|u| u.kind != "text"),
        "outline supports Go, Python, JavaScript, TypeScript, and Rust sources"
    );
    let functions: Vec<OutlineEntry> = units
        .iter()
        .filter(|u| u.kind == "function")
        .map(|u| {
            let first = u.text.lines().next().unwrap_or("").trim();
            let signature = outline_signature(path, u.name.as_deref(), first);
            OutlineEntry {
                start: u.start,
                end: u.end,
                name: u.name.clone(),
                signature,
            }
        })
        .collect();
    let lines = text.lines().count();
    let mut context = format!("{path} ({lines} lines, {} functions)\n", functions.len());
    for f in &functions {
        context.push_str(&format!("L{}-{}: {}\n", f.start, f.end, f.signature));
    }
    Ok(Outline {
        path: path.into(),
        lines,
        functions,
        context,
    })
}

/// Return a page of whole outline entries within an explicit cl100k token budget.
/// `offset` and `limit` address function entries, not source lines. Entries are kept
/// in source order, and an entry is included only when its complete rendered line fits.
pub fn outline_budgeted(
    bpe: &CoreBPE,
    index: &Index,
    path: &str,
    budget: usize,
    offset: usize,
    limit: usize,
) -> Result<BudgetedOutline> {
    let text = index.current_text(path)?;
    let units = code_units(path, &text);
    ensure!(
        units.iter().all(|u| u.kind != "text"),
        "outline supports Go, Python, JavaScript, TypeScript, and Rust sources"
    );
    let all: Vec<OutlineEntry> = units
        .iter()
        .filter(|u| u.kind == "function")
        .map(|u| {
            let first = u.text.lines().next().unwrap_or("").trim();
            OutlineEntry {
                start: u.start,
                end: u.end,
                name: u.name.clone(),
                signature: outline_signature(path, u.name.as_deref(), first),
            }
        })
        .collect();
    let total_functions = all.len();
    let lines = text.lines().count();
    let initial = format!("{path} ({lines} lines, {total_functions} functions)\n");
    let mut context = String::new();
    let mut functions = Vec::new();
    let mut cursor = offset.min(total_functions);
    let header_fits = bpe.encode_ordinary(&initial).len() <= budget;
    if header_fits {
        context.push_str(&initial);
        let end = total_functions.min(offset.saturating_add(limit));
        while cursor < end {
            let entry = &all[cursor];
            let rendered = format!("L{}-{}: {}\n", entry.start, entry.end, entry.signature);
            let mut candidate = context.clone();
            candidate.push_str(&rendered);
            let has_more_after = cursor + 1 < total_functions;
            if has_more_after {
                candidate.push_str(&continuation(cursor + 1));
            }
            if bpe.encode_ordinary(&candidate).len() > budget {
                break;
            }
            if has_more_after {
                candidate.truncate(candidate.len() - continuation(cursor + 1).len());
            }
            context = candidate;
            functions.push(OutlineEntry {
                start: entry.start,
                end: entry.end,
                name: entry.name.clone(),
                signature: entry.signature.clone(),
            });
            cursor += 1;
        }
    }
    if cursor < total_functions {
        let footer = continuation(cursor);
        let mut candidate = context.clone();
        candidate.push_str(&footer);
        if bpe.encode_ordinary(&candidate).len() <= budget {
            context = candidate;
        } else {
            // Keep the continuation visible when the header/signatures consume the page.
            if bpe.encode_ordinary(&footer).len() <= budget {
                context = footer;
                functions.clear();
                cursor = offset.min(total_functions);
            } else {
                context.clear();
                functions.clear();
                cursor = offset.min(total_functions);
            }
        }
    }
    let next_entry = (cursor < total_functions).then_some(cursor);
    let truncated = next_entry.is_some();
    let context_tokens = bpe.encode_ordinary(&context).len();
    Ok(BudgetedOutline {
        path: path.into(),
        lines,
        functions,
        context,
        budget,
        context_tokens,
        offset,
        limit,
        next_entry,
        next_offset: next_entry,
        truncated,
        total_functions,
    })
}

fn continuation(next: usize) -> String {
    format!("Continuation: next_offset={next} (increase budget)\n")
}
