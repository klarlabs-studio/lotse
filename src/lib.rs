//! Deterministic lexical retrieval over versioned repository source excerpts.
mod handles;
mod index;
mod pack;
mod policy;
mod query;
mod response;
mod source;
mod terms;

pub use index::{BuildReport, Index, IndexStats, Posting, index_path};
pub use policy::{FileScore, Policy, Stop};
pub use response::{Excerpt, Hint, LocateResponse, Response};
pub use source::{Chunk, Source, digest};
pub use terms::{Analyzer, Stopwords, terms};

use anyhow::{Context, Result, bail};
use std::path::{Path, PathBuf};

pub fn canonical_root(root: &Path) -> Result<PathBuf> {
    let root = root
        .canonicalize()
        .context("Repository root does not exist")?;
    if !root.is_dir() {
        bail!("Repository root must be a directory");
    }
    Ok(root)
}
