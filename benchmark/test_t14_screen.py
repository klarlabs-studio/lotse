from t14_screen import interleaved


def test_languages_take_turns_until_each_runs_out():
    order = {"Go": ["g1", "g2"], "Python": ["p1"], "JavaScript": ["j1", "j2", "j3"]}
    assert list(interleaved(order)) == ["g1", "p1", "j1", "g2", "j2", "j3"]
