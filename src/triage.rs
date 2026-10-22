//! Experimental agent orientation: `triage` (where to work) and `outline` (what a file holds).
//!
//! `triage` composes two screened arms in one answer: files ranked by `locate`
//! with damped file scores, the floor stop, and the rewritten query
//! (`graf_floor_ids`), then whole functions from `units` with the rewritten query
//! (`ast_graph_ids`). `outline` lists a file's function units so an agent can read
//! one function instead of the whole file. Neither is an accepted improvement.
use crate::rewrite::rewrite_query;
use crate::units::{Unit, code_units, retrieve};
use crate::{FileScore, Index, Policy, Stop};
use anyhow::{Result, ensure};
use serde::Serialize;
use std::collections::BTreeMap;
use tiktoken_rs::CoreBPE;

/// Files listed before the code.
pub const TRIAGE_FILES: usize = 8;
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

/// Every indexed file's current text, refusing stale snapshots.
pub fn indexed_texts(index: &Index) -> Result<BTreeMap<String, String>> {
    index
        .files
        .keys()
        .map(|path| Ok((path.clone(), index.current_text(path)?)))
        .collect()
}

/// Likely files, then whole likely functions, within `budget` cl100k tokens in total.
pub fn triage(bpe: &CoreBPE, index: &Index, issue: &str, budget: usize) -> Result<Triage> {
    let query = rewrite_query(issue);
    let policy = Policy {
        stop: Stop::Floor,
        file_score: FileScore::Damped,
    };
    let located = index.locate_with(&query, 8000, 1000, None, None, policy)?;
    let mut files: Vec<String> = located
        .hints
        .iter()
        .map(|h| h.path.clone())
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
    let mut units = Vec::new();
    if remaining > 0 {
        let (picked, code) = retrieve(bpe, &indexed_texts(index)?, &query, remaining);
        if !code.is_empty() {
            context.push_str(header);
            context.push_str(&code);
            units = picked.iter().map(CitedUnit::from).collect();
        }
    }
    Ok(Triage {
        query,
        files,
        units,
        budget,
        context_tokens: bpe.encode_ordinary(&context).len(),
        context,
        experimental: "triage: graf_floor_ids files, then ast_graph_ids units",
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
        "outline supports Go, Python, and JavaScript sources"
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
