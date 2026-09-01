"""Keep the regular-user shell on real routes and public contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "playground" / "ui" / "src" / "main.jsx").read_text()
STYLES = (ROOT / "playground" / "ui" / "src" / "styles.css").read_text()
HTTP = (ROOT / "hyperset" / "transport" / "http.py").read_text()
ADR_INDEX = (ROOT / "docs" / "adr" / "README.md").read_text()
UX_PAGE_MAP = (ROOT / "docs" / "ux" / "v1-page-map-and-persona-flows.md").read_text()


def test_user_shell_has_persistent_navigation_and_quiet_home():
    for label in ("Live chat", "Explore the Hive-Mind", "Review", "Settings"):
        assert label in UI
    assert 'const USER_PAGE_KEYS = new Set(["explore"])' in UI
    assert 'href="/playground/"' in UI
    assert "writeThreadRestore(localStorage, threadRestorePayload(thread))" in UI


def test_playground_and_settings_use_compact_accessible_dropdowns():
    assert 'const DEBUG_TABS = [["chat", "Live chat"]];' in UI
    assert "const ADMIN_TABS" not in UI
    assert 'aria-label="Settings views"' not in UI
    assert "function adminTabFromPath()" in UI
    assert 'return "readiness";' in UI


def test_explorer_uses_catalog_and_exact_context_bundle_resolution():
    assert 'requestJson("/v0/list_context_catalog", { limit: 200, offset: 0 })' in UI
    assert 'requestJson("/v0/resolve_analytics_context"' in UI
    assert "directive: { domains: [entry.domain], concepts: entry.concepts || [] }" in UI
    for field in ("context_authority", "approved_sources", "domain_graph", "commit_sha"):
        assert field in UI
    assert 'aria-label="Search domains and concepts"' in UI
    assert "Filter nodes" in UI


def test_http_serves_client_routes_without_creating_agent_operations():
    assert 'return first_segment not in {"api", "assets"}' in HTTP
    assert 'href="/playground/"' in HTTP
    assert 'href="/review/"' in HTTP
    assert 'href="/admin/"' in HTTP
    assert "_MCP_SETUP_PATH" in HTTP
    assert 'const MCP_DOCS_URL = "/admin/#mcp"' in UI
    assert ".shell-page" in STYLES
    assert ".explorer-layout" in STYLES


def test_review_heading_wraps_with_its_content_column():
    heading_rule = STYLES.split(".review-page-heading {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in heading_rule


def test_local_docs_are_packaged_for_the_runtime_image():
    dockerfile = (ROOT / "docker" / "hyperset.Dockerfile").read_text()
    assert "COPY docs ./docs" in dockerfile
    assert "PLAYGROUND_DOCS_PATH" in HTTP


def test_adr_index_and_ux_contract_document_the_current_shell():
    assert "[0038](0038-playground-review-settings-navigation.md)" in ADR_INDEX
    assert "[ADR 0038](../adr/0038-playground-review-settings-navigation.md)" in UX_PAGE_MAP
    assert "Visibility in this local shell is not authorization" in UX_PAGE_MAP
