//! Source files, their fixed-size windows, and path classification.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;

pub(crate) const CHUNK_LINES: usize = 24;

pub fn digest(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Chunk {
    pub id: String,
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Source {
    pub hash: String,
    pub bytes: usize,
    pub lines: usize,
    pub kind: String,
    pub chunks: Vec<Chunk>,
}

impl Source {
    /// Split `text` (whose SHA-256 is `hash`) into nonoverlapping windows, omitting blank ones.
    pub(crate) fn new(path: &str, text: &str, hash: String) -> Self {
        let lines: Vec<&str> = text.lines().collect();
        let chunks = lines
            .chunks(CHUNK_LINES)
            .enumerate()
            .filter_map(|(i, lines)| {
                let text = lines.join("\n");
                if text.trim().is_empty() {
                    return None;
                }
                let start = i * CHUNK_LINES + 1;
                let end = start + lines.len() - 1;
                let id = digest(&format!("{path}\0{hash}\0{start}\0{end}"));
                Some(Chunk {
                    id,
                    path: path.into(),
                    start,
                    end,
                    text,
                })
            })
            .collect();
        Self {
            hash,
            bytes: text.len(),
            lines: lines.len(),
            kind: kind(path).into(),
            chunks,
        }
    }
}

/// Path/extension heuristics, not semantic product-intent labels.
pub(crate) fn kind(path: &str) -> &'static str {
    let extension = Path::new(path)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    if matches!(extension, "md" | "mdx" | "rst" | "txt" | "adoc") {
        "docs"
    } else if is_test(path) {
        "test"
    } else {
        "code"
    }
}

fn is_test(path: &str) -> bool {
    path.split('/')
        .any(|p| matches!(p, "test" | "tests" | "__tests__"))
        || path.contains(".test.")
        || path.contains(".spec.")
        || path.ends_with("_test.go")
        || Path::new(path)
            .file_name()
            .is_some_and(|p| p.to_string_lossy().starts_with("test_"))
}

pub(crate) fn supported(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|s| s.to_str()),
        Some(
            "rs" | "go"
                | "py"
                | "js"
                | "jsx"
                | "ts"
                | "tsx"
                | "mjs"
                | "cjs"
                | "java"
                | "kt"
                | "kts"
                | "c"
                | "h"
                | "cpp"
                | "hpp"
                | "cc"
                | "cs"
                | "rb"
                | "php"
                | "swift"
                | "scala"
                | "sh"
                | "bash"
                | "sql"
                | "proto"
                | "md"
                | "mdx"
                | "rst"
                | "txt"
                | "adoc"
                | "toml"
                | "yaml"
                | "yml"
                | "json"
                | "html"
                | "css"
                | "scss"
                | "vue"
                | "svelte"
        )
    ) || matches!(
        path.file_name().and_then(|s| s.to_str()),
        Some("Dockerfile" | "Makefile")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_docs_tests_and_code_by_path() {
        for (path, expected) in [
            ("README.md", "docs"),
            ("docs/guide.rst", "docs"),
            ("tests/conftest.py", "test"),
            ("src/__tests__/a.js", "test"),
            ("lib/route.test.js", "test"),
            ("app.spec.ts", "test"),
            ("args_test.go", "test"),
            ("pkg/test_app.py", "test"),
            ("tests/README.md", "docs"),
            ("src/contest.rs", "code"),
            ("latest/app.py", "code"),
        ] {
            assert_eq!(kind(path), expected, "{path}");
        }
    }

    #[test]
    fn windows_skip_blank_runs_and_cite_inclusive_lines() {
        let text = format!("{}{}last", " \n".repeat(24), "x\n".repeat(24));
        let source = Source::new("a.rs", &text, digest(&text));
        let ranges: Vec<_> = source.chunks.iter().map(|c| (c.start, c.end)).collect();
        assert_eq!(ranges, [(25, 48), (49, 49)]);
        assert_eq!(source.lines, 49);
        assert_eq!(source.chunks[1].text, "last");
    }

    #[test]
    fn supports_listed_extensions_and_build_files_only() {
        assert!(supported(Path::new("a/b.rs")));
        assert!(supported(Path::new("Makefile")));
        assert!(supported(Path::new("x/Dockerfile")));
        assert!(!supported(Path::new("image.png")));
        assert!(!supported(Path::new("LICENSE")));
    }
}
