//! Experimental agent orientation: `triage` (where to work) and `outline` (what a file holds).
//!
//! `triage` composes two screened arms in one answer: files ranked by `locate`
//! file scores (damped, floor stop, rewritten query) raised over cross-file call
//! edges (`file_graph_ids`, preregistration v24), then whole functions from `units`
//! with the rewritten query (`ast_graph_ids`). `outline` lists a file's function units so an agent can read
//! one function instead of the whole file. Neither is an accepted improvement.
use crate::rewrite::rewrite_query;
use crate::units::{
    FLOOR, Unit, WEIGHT, call_edges, code_units, file_propagate, pack_units, packing_candidates,
    scores_counted, term_counts, unit_text,
};
use crate::{FileScore, Index, Policy, Stop};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use tiktoken_rs::CoreBPE;

/// Changes whenever unit extraction could change; a mismatching cache is rebuilt.
const UNIT_CACHE_VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), "/units-3");

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

/// Every indexed unit in path order, with its cached term counts.
pub struct IndexedUnits {
    pub units: Vec<Unit>,
    pub counts: Vec<Vec<(String, u32)>>,
}

/// Files listed before the code.
pub const TRIAGE_FILES: usize = 8;
/// The locate budget the file-ranking screen used to collect file scores.
const LOCATE_BUDGET: usize = 2000;
/// Characters of each signature line in an outline.
const SIGNATURE_CHARS: usize = 120;

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

/// Every indexed file's units, in path order. Units are cached in `.graf/units.bin`
/// by source hash, so only new or changed files are parsed.
pub fn indexed_units(index: &Index) -> Result<IndexedUnits> {
    let path = index.root.join(".graf/units.bin");
    let mut cache: UnitCache = fs::read(&path)
        .ok()
        .and_then(|bytes| bincode::deserialize(&bytes).ok())
        .filter(|c: &UnitCache| c.version == UNIT_CACHE_VERSION)
        .unwrap_or_default();
    let mut fresh = BTreeMap::new();
    let mut changed = false;
    for (file, source) in &index.files {
        let entry = match cache.files.remove(file) {
            Some(cached) if cached.hash == source.hash => cached,
            _ => {
                changed = true;
                let text = index.current_text(file)?;
                let mut units = code_units(file, &text);
                let counts = units.iter().map(|u| term_counts(&u.text)).collect();
                for unit in &mut units {
                    unit.text.clear();
                }
                CachedUnits {
                    hash: source.hash.clone(),
                    units,
                    counts,
                }
            }
        };
        fresh.insert(file.clone(), entry);
    }
    let indexed = IndexedUnits {
        units: fresh
            .values()
            .flat_map(|c| c.units.iter().cloned())
            .collect(),
        counts: fresh
            .values()
            .flat_map(|c| c.counts.iter().cloned())
            .collect(),
    };
    if changed || !cache.files.is_empty() {
        save_cache(
            index,
            &UnitCache {
                version: UNIT_CACHE_VERSION.into(),
                files: fresh,
            },
        )?;
    }
    Ok(indexed)
}

fn save_cache(index: &Index, cache: &UnitCache) -> Result<()> {
    let dir = index.root.join(".graf");
    ensure!(
        !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
        ".graf must not be a symlink"
    );
    let mut temp = tempfile::NamedTempFile::new_in(&dir)?;
    temp.write_all(&bincode::serialize(cache)?)?;
    temp.persist(dir.join("units.bin"))
        .map_err(|e| e.error)
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
    ranked_from(bpe, index, indexed_units(index)?, query, budget)
}

fn ranked_from(
    bpe: &CoreBPE,
    index: &Index,
    indexed: IndexedUnits,
    query: &str,
    budget: usize,
) -> Result<(Vec<Unit>, String)> {
    let scores = scores_counted(&indexed.units, &indexed.counts, query);
    // Only units that packing can reach need their text; read it from the checked source.
    let mut units = indexed.units;
    let mut texts: BTreeMap<String, String> = BTreeMap::new();
    for i in packing_candidates(&scores) {
        let path = units[i].path.clone();
        if !texts.contains_key(&path) {
            let text = index.current_text(&path)?;
            texts.insert(path.clone(), text);
        }
        units[i].text = unit_text(&texts[&path], units[i].start, units[i].end);
    }
    Ok(pack_units(bpe, &units, &scores, budget, FLOOR))
}

/// Likely files, then whole likely functions, within `budget` cl100k tokens in total.
pub fn triage(bpe: &CoreBPE, index: &Index, issue: &str, budget: usize) -> Result<Triage> {
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
    let indexed = indexed_units(index)?;
    let mut files: Vec<String> =
        file_propagate(&scores, &indexed.units, &call_edges(&indexed.units), WEIGHT)
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

/// The functions of an indexed source file, each with its line range and first line.
pub fn outline(index: &Index, path: &str) -> Result<Outline> {
    let text = index.current_text(path)?;
    let units = code_units(path, &text);
    ensure!(
        units.iter().all(|u| u.kind != "text"),
        "outline supports Go, Python, JavaScript, and TypeScript sources"
    );
    let functions: Vec<OutlineEntry> = units
        .iter()
        .filter(|u| u.kind == "function")
        .map(|u| {
            let first = u.text.lines().next().unwrap_or("").trim();
            OutlineEntry {
                start: u.start,
                end: u.end,
                name: u.name.clone(),
                signature: first.chars().take(SIGNATURE_CHARS).collect(),
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
