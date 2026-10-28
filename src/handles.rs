//! Short, globally unique prefixes of content-derived chunk IDs.
use std::collections::BTreeMap;

pub(crate) const MIN_HANDLE: usize = 12;

/// Neighbors in sorted hash order determine the shortest unique prefix.
/// Consider the entire index so scopes and response budgets cannot change handles.
pub(crate) fn compact_handles<'a>(
    ids: impl Iterator<Item = &'a str>,
) -> BTreeMap<&'a str, &'a str> {
    let mut ids: Vec<_> = ids.collect();
    ids.sort_unstable();
    ids.iter()
        .enumerate()
        .map(|(i, &id)| {
            let required = [i.checked_sub(1), (i + 1 < ids.len()).then_some(i + 1)]
                .into_iter()
                .flatten()
                .map(|j| shared_prefix(id, ids[j]) + 1)
                .max()
                .unwrap_or(0)
                .max(MIN_HANDLE)
                .min(id.len());
            (id, &id[..required])
        })
        .collect()
}

fn shared_prefix(a: &str, b: &str) -> usize {
    a.bytes().zip(b.bytes()).take_while(|(a, b)| a == b).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefixes_extend_past_collisions_and_ignore_input_order() {
        let a = format!("{}0{}", "a".repeat(12), "0".repeat(51));
        let b = format!("{}1{}", "a".repeat(12), "0".repeat(51));
        let c = "b".repeat(64);
        let ids = [a.as_str(), b.as_str(), c.as_str()];
        let handles = compact_handles(ids.into_iter());
        assert_eq!(handles[a.as_str()], &a[..13]);
        assert_eq!(handles[b.as_str()], &b[..13]);
        assert_eq!(handles[c.as_str()], &c[..12]);
        assert_eq!(handles, compact_handles(ids.into_iter().rev()));
    }
}
