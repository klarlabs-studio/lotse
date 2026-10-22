use anyhow::{Result, ensure};
use clap::{Parser, Subcommand, ValueEnum};
use graf::{Analyzer, FileScore, Index, Policy, Stop, Stopwords, canonical_root};
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

/// Experimental delivery arms; see research/next-experiments.md.
#[derive(Clone, Copy, ValueEnum)]
enum StopArg {
    /// Offer every ranked candidate until the budget is full (accepted behavior).
    Full,
    /// Arm A1: cut after the largest relative score gap, plus two.
    Gap,
    /// Arm A2: cut below half of the best score.
    Floor,
}

#[derive(Clone, Copy, ValueEnum)]
enum StopwordsArg {
    /// English plus request-phrasing words (accepted behavior).
    Request,
    /// Arm C1: Lucene's default English stop set.
    English,
    /// Arm C2: no stopwords.
    None,
}

#[derive(Clone, Copy, ValueEnum)]
enum FileScoreArg {
    /// Best window per file (accepted behavior).
    Max,
    /// Arm B1: best window plus damped second and third windows.
    Damped,
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
    /// Experimental stopping rule; not an accepted improvement.
    #[arg(long, value_enum, default_value = "full")]
    stop: StopArg,
    /// Experimental file score for locate; not an accepted improvement.
    #[arg(long, value_enum, default_value = "max")]
    file_score: FileScoreArg,
    /// Experimental: query with the issue title plus code-like tokens (see `graf rewrite`).
    #[arg(long)]
    rewrite: bool,
}

impl QueryArgs {
    fn query(&self) -> String {
        if self.rewrite {
            graf::rewrite::rewrite_query(&self.query)
        } else {
            self.query.clone()
        }
    }

    fn policy(&self) -> Policy {
        Policy {
            stop: match self.stop {
                StopArg::Full => Stop::Full,
                StopArg::Gap => Stop::Gap,
                StopArg::Floor => Stop::Floor,
            },
            file_score: match self.file_score {
                FileScoreArg::Max => FileScore::Max,
                FileScoreArg::Damped => FileScore::Damped,
            },
        }
    }
}

#[derive(Subcommand)]
enum Command {
    /// Create/update the index. Reuse unchanged chunks; rebuild lexical postings.
    /// The analyzer flags are experimental arms; without them the accepted analyzer is used.
    Index {
        /// Discard an incompatible or moved index.
        #[arg(long)]
        rebuild: bool,
        /// Experimental stopword list (arms C1/C2); not an accepted improvement.
        #[arg(long, value_enum, default_value = "request")]
        stopwords: StopwordsArg,
        /// Experimental: also index compound identifiers whole (arm C3).
        #[arg(long)]
        full_identifiers: bool,
        /// Experimental: count path terms this many times (arm B2, 1 to 10).
        #[arg(long, default_value_t = 1)]
        path_weight: u32,
    },
    /// Rank source windows with BM25. JSON output includes context and expansion IDs.
    Search(QueryArgs),
    /// Return one compact source hint per file, with handles for expand.
    Locate(QueryArgs),
    /// Experimental: whole function units ranked by BM25 with one-hop call propagation.
    /// A port of the ast_graph screening arm; not an accepted improvement.
    Units {
        query: String,
        #[arg(long, default_value_t = 2000)]
        budget: usize,
        /// Emit only context, without JSON metadata.
        #[arg(long)]
        text: bool,
        /// Experimental: query with the issue title plus code-like tokens.
        #[arg(long)]
        rewrite: bool,
    },
    /// Experimental: where to start on an issue. Lists likely files, then whole likely
    /// functions, within one budget. Pass the issue text (title on the first line).
    Triage {
        issue: String,
        #[arg(long, default_value_t = 2000)]
        budget: usize,
        /// Emit only context, without JSON metadata.
        #[arg(long)]
        text: bool,
    },
    /// Experimental: a source file's functions with line ranges, to read one function, not the file.
    Outline {
        path: String,
        /// Emit only context, without JSON metadata.
        #[arg(long)]
        text: bool,
    },
    /// Experimental: print the query `--rewrite` would use for TEXT (the title plus code-like tokens).
    Rewrite { text: String },
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

/// Text mode writes only the delivered context; otherwise the full JSON response.
fn emit(text: bool, context: &str, response: &impl Serialize) -> Result<()> {
    if text {
        write_stdout(context)
    } else {
        print_json(response)
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    let root = canonical_root(&cli.root)?;
    if let Command::Index {
        rebuild,
        stopwords,
        full_identifiers,
        path_weight,
    } = cli.command
    {
        let analyzer = Analyzer {
            stopwords: match stopwords {
                StopwordsArg::Request => Stopwords::Request,
                StopwordsArg::English => Stopwords::English,
                StopwordsArg::None => Stopwords::None,
            },
            full_identifiers,
            path_weight,
        };
        return print_json(&Index::build_with(&root, rebuild, analyzer)?);
    }
    if let Command::Rewrite { text } = &cli.command {
        return write_stdout(&format!("{}\n", graf::rewrite::rewrite_query(text)));
    }
    let index = Index::load(&root)?;
    match cli.command {
        Command::Search(args) => {
            ensure!(
                matches!(args.file_score, FileScoreArg::Max),
                "--file-score applies to locate only"
            );
            let response = index.search_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::Locate(args) => {
            let response = index.locate_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
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
        Command::Units {
            query,
            budget,
            text,
            rewrite,
        } => {
            let query = if rewrite {
                graf::rewrite::rewrite_query(&query)
            } else {
                query
            };
            let bpe = tiktoken_rs::cl100k_base()?;
            let (units, context) = graf::triage::ranked_units(&bpe, &index, &query, budget)?;
            let cited: Vec<_> = units
                .iter()
                .map(|u| serde_json::json!({"path": u.path, "start": u.start, "end": u.end, "name": u.name}))
                .collect();
            emit(
                text,
                &context,
                &serde_json::json!({"context": context, "units": cited, "budget": budget,
                                    "experimental": "units: tree-sitter units with call propagation"}),
            )
        }
        Command::Triage {
            issue,
            budget,
            text,
        } => {
            let bpe = tiktoken_rs::cl100k_base()?;
            let response = graf::triage::triage(&bpe, &index, &issue, budget)?;
            emit(text, &response.context, &response)
        }
        Command::Outline { path, text } => {
            let response = graf::triage::outline(&index, &path)?;
            emit(text, &response.context, &response)
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
        Command::Index { .. } | Command::Rewrite { .. } => unreachable!(),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("graf: {error:#}");
        std::process::exit(1);
    }
}
