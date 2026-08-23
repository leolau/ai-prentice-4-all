from app_mcp.guard import looks_destructive


def test_matches_destructive_verbs():
    assert looks_destructive("Delete")
    assert looks_destructive("Delete task")
    assert looks_destructive("Archive conversation")
    assert looks_destructive("Send")
    assert looks_destructive("Submit run")
    assert looks_destructive("Remove member")
    assert looks_destructive("please DELETE this")


def test_ignores_safe_labels():
    assert not looks_destructive("Filter")
    assert not looks_destructive("Chats")
    assert not looks_destructive("Open task")
    assert not looks_destructive("Sender settings")  # word boundary, not substring
    assert not looks_destructive("Deliverable notes")  # boundary: not "deliver"
    assert not looks_destructive("")
    assert not looks_destructive(None)


def test_boundary_not_substring():
    # "post" matches as a word, but "postpone" must not.
    assert looks_destructive("Post update")
    assert not looks_destructive("Postpone reminder")
