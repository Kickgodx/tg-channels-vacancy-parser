from tg_jobs_parser.utils.text import post_id_display


def test_post_id_display_suffix():
    assert post_id_display("qa_jobs-255541") == "255541"
    assert post_id_display("qa-jobs-23131") == "23131"


def test_post_id_display_fallback():
    assert post_id_display("") == ""
    assert post_id_display("no-hyphen") == "no-hyphen"
