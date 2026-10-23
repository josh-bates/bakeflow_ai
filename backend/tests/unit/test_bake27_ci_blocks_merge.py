"""BAKE-27 manual check: this deliberately failing test must block the PR. Never merge."""


def test_deliberately_fails_to_show_ci_blocks_the_merge():
    assert False, "BAKE-27: deliberate failure; this PR must stay unmergeable"
