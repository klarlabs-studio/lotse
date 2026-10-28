use anyhow::{Result, ensure};
use clap::{Parser, Subcommand, ValueEnum};
use graf::{Analyzer, FileScore, Index, Policy, Stop, Stopwords, canonical_root};
use serde::Serialize;
use std::io::{self, Write};
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    version,
    about = "Local source evidence for agents. Code and documentation; no model required.",
    after_help = "Typical agent workflow:\n  \
        graf triage \"<issue or task text>\"   likely files, then likely functions in full\n  \
        graf outline <path>                 a file's functions with line ranges\n  \
        graf units \"<follow-up query>\"      more whole functions\n\n\
        The index is built on first use and lives in .graf/; run `graf index` after edits.\n\
        For MCP clients: `claude mcp add graf -- graf mcp` (see `graf mcp --help`)."
)]
struct Cli {
    /// Repository root. The index lives in ROOT/.graf/.
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
    /// Text that starts with `-` is still read as text, not as an option.
    #[arg(allow_hyphen_values = true)]
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
    /// Create/update the index. Reuse unchanged chunks and matching-analyzer postings.
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
    /// Experimental: locate-ranked files, then equally budgeted exact source windows.
    Evidence(QueryArgs),
    /// Experimental: complete fitting source units within locate-ranked files.
    EvidenceUnits(QueryArgs),
    /// Experimental: append fitting units from up to four cross-file call neighbours.
    EvidenceLinked(QueryArgs),
    /// Experimental: atomic linked functions with up to two direct local helpers.
    EvidenceBundles(QueryArgs),
    /// Experimental: reward distinct IDF-weighted query coverage before source delivery.
    EvidenceCoverage(QueryArgs),
    /// Experimental: bounded lexical families before structural source delivery.
    EvidenceFamilies(QueryArgs),
    /// Experimental: strongest full-file lexical passage within family-ranked files.
    EvidencePassages(QueryArgs),
    /// Experimental: preserve structural evidence and append novel-term passages.
    EvidenceComplement(QueryArgs),
    /// Experimental: interleave code, documentation and test file relevance ranks.
    EvidenceStratified(QueryArgs),
    /// Experimental: fuse chunk and symbol file ranks before artifact interleave.
    EvidenceFused(QueryArgs),
    /// Experimental: resolve explicit documentation symbols into source file ranks.
    EvidenceReferenced(QueryArgs),
    /// Experimental: preserve fused evidence and append fitting referenced functions.
    EvidenceRescue(QueryArgs),
    /// Experimental: append fitting direct local callees to rescued evidence.
    EvidenceCallees(QueryArgs),
    /// Experimental: one query-matching local callee with bounded added tokens.
    EvidenceSelective(QueryArgs),
    /// Experimental: whole function units ranked by BM25 with one-hop call propagation.
    /// A port of the ast_graph screening arm; not an accepted improvement.
    Units {
        /// Text that starts with `-` is still read as text, not as an option.
        #[arg(allow_hyphen_values = true)]
        query: String,
        #[arg(long, default_value_t = 2000)]
        budget: usize,
        /// Emit only context, without JSON metadata.
        #[arg(long)]
        text: bool,
        /// Experimental: query with the issue title plus code-like tokens.
        #[arg(long)]
        rewrite: bool,
        /// Experimental: center an oversized first unit on its strongest query line.
        #[arg(long)]
        focus: bool,
        /// Experimental: also rank exact symbols and file paths from the query.
        #[arg(long)]
        symbols: bool,
        /// Experimental: retain candidates passing the body score cutoff as well.
        #[arg(long, requires = "symbols")]
        retain_body: bool,
        /// Experimental: visit each candidate file before repeated units.
        #[arg(long, requires_all = ["symbols", "retain_body", "focus"])]
        file_diversity: bool,
        /// Experimental: include up to two directly called same-file definitions.
        #[arg(long, requires_all = ["symbols", "retain_body", "focus", "file_diversity"])]
        linked: bool,
        /// Experimental: append helpers only within unused file-diverse budget.
        #[arg(long, requires_all = ["symbols", "retain_body", "focus", "file_diversity"], conflicts_with = "linked")]
        linked_tail: bool,
    },
    /// Experimental: where to start on an issue. Lists likely files, then whole likely
    /// functions, within one budget. Pass the issue text (title on the first line).
    Triage {
        /// Text that starts with `-` is still read as text, not as an option.
        #[arg(allow_hyphen_values = true)]
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
        /// Cap a page of complete entries in cl100k tokens, including continuation hints.
        #[arg(long)]
        budget: Option<usize>,
        /// Zero-based function entry to start from (not a source line).
        #[arg(long, default_value_t = 0)]
        offset: usize,
        /// Maximum functions in a page. Enables paging with a default budget of 1,500.
        #[arg(long)]
        limit: Option<usize>,
        /// Emit only context, without JSON metadata.
        #[arg(long)]
        text: bool,
    },
    /// Serve graf's tools to an agent over the Model Context Protocol (stdio).
    ///
    /// Register it once, for example `claude mcp add graf -- graf mcp`. The server
    /// indexes the repository on first use and keeps the index current as files change.
    Mcp,
    /// Experimental: print the query `--rewrite` would use for TEXT (the title plus code-like tokens).
    Rewrite {
        #[arg(allow_hyphen_values = true)]
        text: String,
    },
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
    /// Print the index in the schema 1 JSON layout (excerpt text included), for tooling.
    #[command(hide = true)]
    ExportIndex,
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
        let report = Index::build_with(&root, rebuild, analyzer)?;
        // Build the function index now, so the first units/triage query is fast.
        graf::triage::indexed_units(&Index::load(&root)?)?;
        return print_json(&report);
    }
    if let Command::Mcp = cli.command {
        return graf::mcp::serve(&root, io::stdin().lock(), io::stdout().lock());
    }
    if let Command::Rewrite { text } = &cli.command {
        return write_stdout(&format!("{}\n", graf::rewrite::rewrite_query(text)));
    }
    // Queries index on first use; stats and map describe an existing index only.
    let queries = !matches!(
        cli.command,
        Command::Stats | Command::Map { .. } | Command::ExportIndex
    );
    if queries && !graf::index_path(&root).exists() {
        eprintln!("graf: no index yet; indexing {} ...", root.display());
        Index::build(&root, false)?;
        graf::triage::indexed_units(&Index::load(&root)?)?;
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
        Command::Evidence(args) => {
            let response = index.evidence_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceUnits(args) => {
            let response = index.evidence_units_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceLinked(args) => {
            let response = index.evidence_linked_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceBundles(args) => {
            let response = index.evidence_bundles_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceCoverage(args) => {
            let response = index.evidence_coverage_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceFamilies(args) => {
            let response = index.evidence_families_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidencePassages(args) => {
            let response = index.evidence_passages_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceComplement(args) => {
            let response = index.evidence_complement_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceStratified(args) => {
            let response = index.evidence_stratified_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceFused(args) => {
            let response = index.evidence_fused_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceReferenced(args) => {
            let response = index.evidence_referenced_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceRescue(args) => {
            let response = index.evidence_rescue_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceCallees(args) => {
            let response = index.evidence_callees_with(
                &args.query(),
                args.budget,
                args.limit,
                args.scope.as_deref(),
                args.kind.as_ref().map(Kind::as_str),
                args.policy(),
            )?;
            emit(args.text, &response.context, &response)
        }
        Command::EvidenceSelective(args) => {
            let response = index.evidence_selective_with(
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
            focus,
            symbols,
            retain_body,
            file_diversity,
            linked,
            linked_tail,
        } => {
            let query = if rewrite {
                graf::rewrite::rewrite_query(&query)
            } else {
                query
            };
            let bpe = tiktoken_rs::cl100k_base()?;
            let (units, context) = if linked_tail {
                graf::triage::ranked_units_linked_tail(&bpe, &index, &query, budget)?
            } else if linked {
                graf::triage::ranked_units_linked(&bpe, &index, &query, budget)?
            } else if file_diversity {
                graf::triage::ranked_units_file_diverse(&bpe, &index, &query, budget)?
            } else if retain_body {
                graf::triage::ranked_units_retaining_body(&bpe, &index, &query, budget, focus)?
            } else if symbols {
                graf::triage::ranked_units_symbols(&bpe, &index, &query, budget, focus)?
            } else if focus {
                graf::triage::ranked_units_focused(&bpe, &index, &query, budget)?
            } else {
                graf::triage::ranked_units(&bpe, &index, &query, budget)?
            };
            let cited: Vec<_> = units
                .iter()
                .map(|u| serde_json::json!({"path": u.path, "start": u.start, "end": u.end, "name": u.name}))
                .collect();
            let mut response = serde_json::json!({"context": context, "units": cited, "budget": budget,
                                    "focus": focus,
                                    "experimental": "units: tree-sitter units with call propagation"});
            if symbols {
                response["symbols"] = true.into();
            }
            if retain_body {
                response["retain_body"] = true.into();
            }
            if file_diversity {
                response["file_diversity"] = true.into();
            }
            if linked {
                response["linked"] = true.into();
            }
            if linked_tail {
                response["linked_tail"] = true.into();
            }
            emit(text, &context, &response)
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
        Command::Outline {
            path,
            budget,
            offset,
            limit,
            text,
        } => {
            if budget.is_some() || offset != 0 || limit.is_some() {
                let limit = limit.unwrap_or(100);
                ensure!(
                    (1..=1000).contains(&limit),
                    "limit must be between 1 and 1000"
                );
                let bpe = tiktoken_rs::cl100k_base()?;
                let response = graf::triage::outline_budgeted(
                    &bpe,
                    &index,
                    &path,
                    budget.unwrap_or(1500),
                    offset,
                    limit,
                )?;
                emit(text, &response.context, &response)
            } else {
                let response = graf::triage::outline(&index, &path)?;
                emit(text, &response.context, &response)
            }
        }
        Command::Stats => print_json(&index.stats()?),
        Command::ExportIndex => print_json(&index.export_legacy()?),
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
        Command::Index { .. } | Command::Mcp | Command::Rewrite { .. } => unreachable!(),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("graf: {error:#}");
        std::process::exit(1);
    }
}
