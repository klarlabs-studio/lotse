//! Serialized command results. Field order is part of the JSON output contract.
use serde::Serialize;

pub(crate) const TOKENIZER: &str = "cl100k_base";

/// Outcome of a retrieval request. Neither failure status is semantic abstention.
pub(crate) fn status(matched: usize, delivered: usize) -> String {
    if matched == 0 {
        "no_lexical_match"
    } else if delivered == 0 {
        "budget_exhausted"
    } else {
        "ok"
    }
    .into()
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
