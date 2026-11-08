from module.files.diff import merge_text, rebuild_text, text_hunks


def test_identical_text_has_no_hunks():
    assert text_hunks("一\n二\n", "一\n二\n") == []


def test_replacement_hunk_carries_line_numbers_and_bodies():
    hunks = text_hunks("一\n二\n三\n", "一\n乙\n三\n")
    assert len(hunks) == 1
    hunk = hunks[0]
    assert (hunk.base_start, hunk.base_end) == (2, 2)
    assert (hunk.staged_start, hunk.staged_end) == (2, 2)
    assert hunk.removed == ("二",)
    assert hunk.added == ("乙",)
    assert hunk.header == "@@ -2,1 +2,1 @@"


def test_insertion_and_deletion_use_empty_ranges():
    inserted = text_hunks("a\nc\n", "a\nb\nc\n")
    assert inserted[0].removed == ()
    assert inserted[0].added == ("b",)
    assert (inserted[0].base_start, inserted[0].base_end) == (2, 1)

    deleted = text_hunks("a\nb\nc\n", "a\nc\n")
    assert deleted[0].removed == ("b",)
    assert deleted[0].added == ()
    assert (deleted[0].staged_start, deleted[0].staged_end) == (2, 1)


def test_trailing_newline_is_part_of_the_diff():
    hunks = text_hunks("a\n", "a")
    assert len(hunks) == 1
    assert hunks[0].removed == ("",)
    assert hunks[0].added == ()


def test_rebuild_uses_only_accepted_hunks():
    base = "一\n二\n三\n"
    staged = "一\n乙\n丙\n"
    hunks = text_hunks(base, staged)
    assert len(hunks) == 1
    assert rebuild_text(base, staged, frozenset()) == base
    assert rebuild_text(base, staged, frozenset({0})) == staged


def test_rebuild_keeps_unaccepted_hunks_in_base_form():
    base = "a\nb\nc\nd\n"
    staged = "A\nb\nc\nD\n"
    hunks = text_hunks(base, staged)
    assert len(hunks) == 2
    assert rebuild_text(base, staged, frozenset({0})) == "A\nb\nc\nd\n"
    assert rebuild_text(base, staged, frozenset({1})) == "a\nb\nc\nD\n"
    assert rebuild_text(base, staged, frozenset({0, 1})) == staged


def test_rebuild_handles_insertion_at_the_end():
    base = "a\n"
    staged = "a\nb\n"
    hunks = text_hunks(base, staged)
    assert len(hunks) == 1
    assert hunks[0].removed == ()
    assert rebuild_text(base, staged, frozenset({0})) == staged


def test_merge_uses_staged_when_disk_matches_base():
    result = merge_text("a\nb\n", "a\nb\n", "a\nB\n")

    assert result.ok is True
    assert result.conflicts == ()
    assert result.text == "a\nB\n"


def test_merge_keeps_disjoint_changes_from_both_sides():
    result = merge_text("a\nb\nc\n", "A\nb\nc\n", "a\nb\nC\n")

    assert result.ok is True
    assert result.text == "A\nb\nC\n"


def test_merge_accepts_the_same_change_from_both_sides():
    result = merge_text("a\nb\n", "A\nb\n", "A\nb\n")

    assert result.ok is True
    assert result.conflicts == ()
    assert result.text == "A\nb\n"


def test_merge_reports_overlapping_changes_with_both_sides():
    result = merge_text("a\nb\nc\n", "a\n磁盘\nc\n", "a\n草稿\nc\n")

    assert result.ok is False
    assert result.text == "a\nb\nc\n"
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert (conflict.base_start, conflict.base_end) == (2, 2)
    assert conflict.disk == ("磁盘",)
    assert conflict.staged == ("草稿",)
    assert conflict.header == "@@ -2,1 @@"


def test_merge_reports_same_position_insertions_as_conflict():
    result = merge_text("a\nb\n", "a\n磁盘\nb\n", "a\n草稿\nb\n")

    assert result.ok is False
    assert len(result.conflicts) == 1
    assert (result.conflicts[0].base_start, result.conflicts[0].base_end) == (2, 1)
    assert result.conflicts[0].disk == ("磁盘",)
    assert result.conflicts[0].staged == ("草稿",)


def test_merge_allows_adjacent_changes():
    result = merge_text("a\nb\nc\n", "A\nb\nc\n", "a\nB\nc\n")

    assert result.ok is True
    assert result.text == "A\nB\nc\n"
