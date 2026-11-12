from app.routers.client_ipv6 import render_profiles


def test_render_adds_replaces_and_removes_only_own_line():
    base = "# комментарий\nbob dual\nalice ipv4\n"
    assert render_profiles(base, "alice", "dual") == "# комментарий\nbob dual\nalice dual\n"
    assert render_profiles(base, "bob", "default") == "# комментарий\nalice ipv4\n"
    assert render_profiles("", "x", "ipv4") == "x ipv4\n"
    assert render_profiles("x ipv4\n", "x", "default") == ""
