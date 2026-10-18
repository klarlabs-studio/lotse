use anyhow::{Result, ensure};
use clap::{Parser, Subcommand, ValueEnum};
use graf::{Index, canonical_root};
use serde::Serialize;
use std::io::{self, Write};
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    version,
    about = "Local source evidence for agents. Code and documentation; no model required."
)]
struct Cli {
    /// Repository root. The index lives in ROOT/.graf/index.json.
    #[arg(long, global = true, default_value = ".")]
    root: PathBuf,
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, ValueEnum)]
enum Kind {
    Code,
    Docs,
    Test,
}
impl Kind {
    fn as_str(&self) -> &str {
        match self {
            Self::Code => "code",
            Self::Docs => "docs",
            Self::Test => "test",
        }
    }
}

#[derive(clap::Args)]
struct QueryArgs {
    query: String,
    /// Exact cl100k_base cap on context with citations; JSON metadata is extra.
    #[arg(long, default_value_t = 2000)]
    budget: usize,
    #[arg(long, default_value_t = 20)]
    limit: usize,
    /// Literal repository-relative path prefix (not a glob).
    #[arg(long)]
    scope: Option<String>,
    #[arg(long, value_enum)]
    kind: Option<Kind>,
    /// Emit only context, without JSON metadata.
    #[arg(long)]
    text: bool,
}

#[derive(Subcommand)]
enum Command {
    /// Create/update the index. Reuse unchanged chunks; rebuild lexical postings.
    Index {
        /// Discard an incompatible or moved index.
        #[arg(long)]
        rebuild: bool,
    },
    /// Rank source windows with BM25. JSON output includes context and expansion IDs.
    Search(QueryArgs),
    /// Return one compact source hint per file, with handles for expand.
    Locate(QueryArgs),
    /// Read an excerpt plus nearby lines using an ID returned by search.
    Expand {
        id: String,
        #[arg(long, default_value_t = 24)]
        before: usize,
        #[arg(long, default_value_t = 24)]
        after: usize,
        #[arg(long, default_value_t = 2000)]
        budget: usize,
        #[arg(long)]
        text: bool,
    },
    /// Page through indexed files; metadata describes the indexed snapshot.
    Map {
        #[arg(long, default_value_t = 0)]
        offset: usize,
        #[arg(long, default_value_t = 50)]
        limit: usize,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long, value_enum)]
        kind: Option<Kind>,
    },
    /// Show index size, source counts, and chunk counts (snapshot metadata).
    Stats,
}

fn write_stdout(text: &str) -> Result<()> {
    match io::stdout().lock().write_all(text.as_bytes()) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::BrokenPipe => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn print_json(value: &impl Serialize) -> Result<()> {
    write_stdout(&format!("{}\n", serde_json::to_string(value)?))
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    let root = canonical_root(&cli.root)?;
    if let Command::Index { rebuild } = cli.command {
        return print_json(&Index::build(&root, rebuild)?);
    }
    let index = Index::load(&root)?;
    match cli.command {
        Command::Search(QueryArgs {
            query,
            budget,
            limit,
            scope,
            kind,
            text,
        }) => {
            ensure!(
                (1..=1000).contains(&limit),
                "limit must be between 1 and 1000"
            );
            let response = index.search(
                &query,
                budget,
                limit,
                scope.as_deref(),
                kind.as_ref().map(Kind::as_str),
            )?;
            if text {
                write_stdout(&response.context)
            } else {
                print_json(&response)
            }
        }
        Command::Locate(QueryArgs {
            query,
            budget,
            limit,
            scope,
            kind,
            text,
        }) => {
            let response = index.locate(
                &query,
                budget,
                limit,
                scope.as_deref(),
                kind.as_ref().map(Kind::as_str),
            )?;
            if text {
                write_stdout(&response.context)
            } else {
                print_json(&response)
            }
        }
        Command::Expand {
            id,
            before,
            after,
            budget,
            text,
        } => {
            let response = index.expand(&id, before, after, budget)?;
            if text {
                write_stdout(&response.context)
            } else {
                print_json(&response)
            }
        }
        Command::Stats => print_json(&index.stats()?),
        Command::Map {
            offset,
            limit,
            scope,
            kind,
        } => {
            ensure!(
                (1..=1000).contains(&limit),
                "limit must be between 1 and 1000"
            );
            let entries: Vec<_> = index
                .files
                .iter()
                .filter(|(path, source)| {
                    scope.as_ref().is_none_or(|s| path.starts_with(s.as_str()))
                        && kind.as_ref().is_none_or(|k| source.kind == k.as_str())
                })
                .collect();
            let files: Vec<_> = entries
                .iter()
                .skip(offset)
                .take(limit)
                .map(|(path, source)| {
                    serde_json::json!({
                        "path": path, "source_hash": source.hash, "kind": source.kind,
                        "lines": source.lines, "chunks": source.chunks.len()
                    })
                })
                .collect();
            print_json(&serde_json::json!({
                "files": files, "total": entries.len(), "offset": offset,
                "next_offset": if offset.saturating_add(limit) < entries.len() { Some(offset + limit) } else { None },
                "freshness": "indexed snapshot; search and expand validate selected source hashes"
            }))
        }
        Command::Index { .. } => unreachable!(),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("graf: {error:#}");
        std::process::exit(1);
    }
}
