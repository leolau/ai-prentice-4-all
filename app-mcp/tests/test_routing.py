from app_mcp.server import _route_for_path, folder_hub, hub


def test_ui_path_routes_to_the_ui_hub():
    assert _route_for_path("/app-mcp/ws") is hub


def test_folder_path_routes_to_the_folder_hub():
    assert _route_for_path("/app-mcp/folders/ws") is folder_hub


def test_trailing_slash_is_tolerated():
    assert _route_for_path("/app-mcp/ws/") is hub
    assert _route_for_path("/app-mcp/folders/ws/") is folder_hub


def test_unknown_path_routes_nowhere():
    assert _route_for_path("/app-mcp/unknown") is None
    assert _route_for_path("/") is None


def test_the_two_hubs_are_actually_distinct_instances():
    # A bug that aliased both routes to the same Hub would pass every other
    # test here while quietly merging UI and folder sessions.
    assert hub is not folder_hub
