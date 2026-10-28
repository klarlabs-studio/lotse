use graf::{Analyzer, Index, MAX_INDEX_BYTES, Stopwords, canonical_root, index_path};
use std::fs;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use tempfile::TempDir;

fn fixture() -> (TempDir, std::path::PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("src")).unwrap();
    fs::write(
        dir.path().join("src/cache.rs"),
        "fn refresh_cache() { let cache = true; }\n",
    )
    .unwrap();
    let root = canonical_root(dir.path()).unwrap();
    (dir, root)
}

fn rewrite(root: &std::path::Path, edit: impl FnOnce(&mut Index)) {
    let path = index_path(root);
    let mut index: Index = bincode::deserialize(&fs::read(&path).unwrap()).unwrap();
    edit(&mut index);
    fs::write(path, bincode::serialize(&index).unwrap()).unwrap();
}

#[test]
fn truncated_and_corrupt_snapshots_rebuild_for_mcp_but_load_stays_strict() {
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    let path = index_path(&root);

    fs::write(&path, b"\x02\0\0").unwrap();
    assert!(Index::load(&root).is_err());
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());
    assert!(Index::load(&root).is_ok());

    fs::write(&path, b"not a bincode index").unwrap();
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());
}

#[test]
fn schema_and_unsafe_snapshot_data_are_rebuilt() {
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();

    rewrite(&root, |index| index.schema = 999);
    assert!(Index::load(&root).is_err());
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());

    rewrite(&root, |index| {
        index.files.get_mut("src/cache.rs").unwrap().chunks[0].path = "../escape.rs".into();
    });
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());

    rewrite(&root, |index| {
        index.postings.values_mut().next().unwrap()[0].chunk = u32::MAX;
    });
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());

    rewrite(&root, |index| {
        let postings = index.postings.values_mut().next().unwrap();
        postings.push(graf::Posting {
            chunk: postings[0].chunk,
            count: postings[0].count,
        });
    });
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());

    rewrite(&root, |index| {
        index.postings.values_mut().next().unwrap()[0].count += 1;
    });
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());

    rewrite(&root, |index| {
        index.files.get_mut("src/cache.rs").unwrap().bytes = 0;
    });
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());

    rewrite(&root, |index| {
        index
            .files
            .insert("../escape.rs".into(), index.files["src/cache.rs"].clone());
    });
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());
    assert!(Index::load(&root).is_ok());
}

#[test]
fn zero_term_chunks_are_valid() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("a.c"), "// a\n").unwrap();
    let root = canonical_root(dir.path()).unwrap();
    Index::build(&root, true).unwrap();

    rewrite(&root, |index| {
        index.lengths[0] = 0;
        index.postings.retain(|_, postings| {
            postings.retain(|posting| posting.chunk != 0);
            !postings.is_empty()
        });
    });
    let loaded = Index::load(&root).unwrap();
    assert_eq!(loaded.lengths, [0]);
    assert!(loaded.postings.is_empty());
    assert!(Index::load_or_rebuild_for_mcp(&root).is_ok());
}

#[test]
fn damaged_postings_preserve_a_valid_experimental_analyzer() {
    let (_dir, root) = fixture();
    let analyzer = Analyzer {
        stopwords: Stopwords::English,
        full_identifiers: true,
        path_weight: 3,
    };
    Index::build_with(&root, true, analyzer).unwrap();
    rewrite(&root, |index| {
        index.postings.values_mut().next().unwrap()[0].count = 0;
    });

    let recovered = Index::load_or_rebuild_for_mcp(&root).unwrap();
    assert_eq!(recovered.analyzer, analyzer);
    assert!(Index::load(&root).is_ok());
}

#[cfg(unix)]
#[test]
fn rebuild_failure_is_reported_when_index_directory_cannot_be_replaced() {
    use std::os::unix::fs::symlink;

    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    let external = tempfile::tempdir().unwrap();
    fs::rename(root.join(".graf"), root.join(".graf.saved")).unwrap();
    symlink(external.path(), root.join(".graf")).unwrap();
    fs::write(index_path(&root), b"broken").unwrap();

    assert!(Index::load_or_rebuild_for_mcp(&root).is_err());
}

#[cfg(unix)]
#[test]
fn index_read_errors_are_propagated() {
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    fs::remove_file(index_path(&root)).unwrap();
    fs::create_dir(index_path(&root)).unwrap();

    let error = Index::load_or_rebuild_for_mcp(&root).unwrap_err();
    assert!(error.to_string().contains("Cannot read index"));
}

#[test]
fn oversized_snapshot_is_a_resource_error_for_both_load_paths() {
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    let path = index_path(&root);
    let file = fs::OpenOptions::new().write(true).open(&path).unwrap();
    file.set_len(MAX_INDEX_BYTES + 1).unwrap();

    for result in [Index::load(&root), Index::load_or_rebuild_for_mcp(&root)] {
        let error = result.unwrap_err();
        assert!(error.to_string().contains("resource limit"));
    }
    assert_eq!(fs::metadata(path).unwrap().len(), MAX_INDEX_BYTES + 1);
}

#[test]
fn a_vs_code_sized_snapshot_is_within_the_limit() {
    // The scale diagnostic's VS Code index was 159 MB (research/scale-results.md).
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    let file = fs::OpenOptions::new()
        .write(true)
        .open(index_path(&root))
        .unwrap();
    file.set_len(159_000_000).unwrap();

    // Trailing bytes are allowed, so the padded snapshot still loads.
    Index::load(&root).unwrap();
}

#[test]
fn invalid_analyzer_falls_back_to_default() {
    let (_dir, root) = fixture();
    let analyzer = Analyzer {
        stopwords: Stopwords::English,
        full_identifiers: true,
        path_weight: 3,
    };
    Index::build_with(&root, true, analyzer).unwrap();
    rewrite(&root, |index| {
        index.analyzer.path_weight = 0;
        index.postings.values_mut().next().unwrap()[0].count = 0;
    });

    let recovered = Index::load_or_rebuild_for_mcp(&root).unwrap();
    assert_eq!(recovered.analyzer, Analyzer::default());
}

#[test]
fn failed_build_keeps_the_previous_snapshot_readable() {
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    let path = index_path(&root);
    let previous = fs::read(&path).unwrap();
    let invalid = Analyzer {
        stopwords: Stopwords::English,
        full_identifiers: false,
        path_weight: 0,
    };

    assert!(Index::build_with(&root, true, invalid).is_err());
    assert_eq!(fs::read(path).unwrap(), previous);
    assert!(Index::load(&root).is_ok());
}

#[test]
fn concurrent_writers_and_reader_only_observe_complete_snapshots() {
    let (_dir, root) = fixture();
    Index::build(&root, true).unwrap();
    let done = Arc::new(AtomicBool::new(false));
    let reads = Arc::new(AtomicUsize::new(0));

    let reader_root = root.clone();
    let reader_done = Arc::clone(&done);
    let reader_reads = Arc::clone(&reads);
    let reader = std::thread::spawn(move || {
        while !reader_done.load(Ordering::Acquire) {
            Index::load(&reader_root).expect("reader saw a complete index");
            reader_reads.fetch_add(1, Ordering::Relaxed);
        }
    });
    let writers: Vec<_> = (0..2)
        .map(|_| {
            let writer_root = root.clone();
            std::thread::spawn(move || {
                for _ in 0..12 {
                    Index::build(&writer_root, true).expect("writer completed snapshot");
                }
            })
        })
        .collect();
    for writer in writers {
        writer.join().unwrap();
    }
    done.store(true, Ordering::Release);
    reader.join().unwrap();
    assert!(reads.load(Ordering::Relaxed) > 0);
    assert!(Index::load(&root).is_ok());
}
