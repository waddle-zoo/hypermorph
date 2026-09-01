"""Keep the reviewed chat runtime contract separate from visual polish.

The shared package has no JavaScript test runner. These focused source checks keep
the behavioral baseline from being replaced by a CSS/mockup refactor again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_UI = (ROOT / "packages" / "chat-ui" / "src" / "index.jsx").read_text()
PLAYGROUND_UI = (ROOT / "playground" / "ui" / "src" / "main.jsx").read_text()


def test_composer_owns_per_turn_context_controls_and_payload():
    """Policy, pinned assets, agent/model selection, and per-turn payload stay live."""
    assert "function RunSettings(" not in CHAT_UI
    assert (
        "function Composer({ onSend, sendDisabled, busy = false, onCancel, admin, agent, model"
    ) in CHAT_UI
    # hy-87n1 (Explorer gap 4): the run mode is an EXPLICIT NAMED choice, not an unlabeled
    # toggle-off. Both states are named and selectable, bound to `governedOnly`, so the old
    # single-checkbox toggle is gone and BOTH option labels are load-bearing.
    assert 'className={`governed-toggle ${governedOnly ? "on" : ""}`}' not in CHAT_UI
    assert 'role="radiogroup" aria-label="Run mode"' in CHAT_UI
    assert "aria-checked={governedOnly}" in CHAT_UI and "aria-checked={!governedOnly}" in CHAT_UI
    assert ">Governed only</button>" in CHAT_UI
    assert ">Governed + observed</button>" in CHAT_UI
    assert 'className="composer-controls"' in CHAT_UI
    assert "onSend(next, { governedOnly, attachments: active })" in CHAT_UI
    assert "governed_only: governedOnly" in CHAT_UI
    assert "attachments" in CHAT_UI


def test_recent_threads_persist_and_restore_run_settings():
    """hy-87n1 (Explorer gap 9): a completed turn is saved WITH the run settings it was sent
    with (agent / model / governed-only), and reopening a Recent thread RESTORES that state,
    not just re-prefills the question. Source pins for the persist + restore path, since the
    shared package has no JS runner in the gate (the behavior is DOM-tested under vitest)."""
    # The turn stamps its run settings, and the save persists them via the shared helper.
    assert "userMessage.settings = { agent, model, governedOnly }" in CHAT_UI
    assert "saveThreadTurn(localStorage, {" in CHAT_UI
    assert "governedOnly: settings.governedOnly" in CHAT_UI
    # The composer restores the reopened thread's run mode from an initial prop.
    assert "initialGovernedOnly" in CHAT_UI

    # SECURITY (hy-87n1 critic, the #472 lesson): chat question/answer are FREE-FORM, so the
    # localStorage WRITE path is redacted at the persist boundary -- both the saved turn and
    # the reopen handoff go through redactDeep before setItem, so no credential is ever
    # written in cleartext. Removing either turns the DOM/stub tests RED; these pin the source.
    assert "const record = redactDeep({" in CHAT_UI
    assert "JSON.stringify(redactDeep(restore))" in CHAT_UI
    # ...and the read path redacts again (defense in depth for legacy cleartext records).
    assert 'redactDeep(JSON.parse(localStorage.getItem("hyperset-threads")' in PLAYGROUND_UI

    # Recent threads hands the whole thread state (question + settings) to the next mount,
    # and the app reads it once and applies agent/model/run-mode -- not just the question.
    assert "writeThreadRestore(localStorage, threadRestorePayload(thread))" in PLAYGROUND_UI
    assert "readThreadRestore(localStorage)" in PLAYGROUND_UI
    assert "initialGovernedOnly={restore ? restore.governedOnly !== false : true}" in PLAYGROUND_UI


def test_streaming_queue_cancel_and_governance_events_remain_rendered():
    """A UI-only polish must not drop any server-streamed chat state."""
    for event_type in (
        "queued",
        "start",
        "stage",
        "selection",
        "bundle",
        "resolution_error",
        "sql",
        "token",
        "done",
        "error",
    ):
        assert f'event.type === "{event_type}"' in CHAT_UI
    assert 'if (error.name === "AbortError")' in CHAT_UI
    assert 'message.status === "streaming"' in CHAT_UI
    assert "controllersRef.current.get(target.id)?.abort()" in CHAT_UI
    assert "event.result.governed_blocked" in CHAT_UI
    assert "error: event.error" in CHAT_UI
    assert "error: error.message" in CHAT_UI
    assert "String(content).split(/(@[^\\s@]+)/g)" in CHAT_UI
    assert "model: selected.value, provider: selected.provider" in CHAT_UI
    assert "governedOnly: false" in CHAT_UI
    assert "requestedGovernedOnly" not in CHAT_UI


def test_chat_discloses_first_class_trust_provenance_and_labeled_states():
    """The per-answer trust/provenance disclosure is FIRST-CLASS, not collapsed JSON
    (hy-icx1, Explorer 5+7). MUTATION-LOAD-BEARING: these pin the rendered CONTENT --
    the state labels, the next-action sentences, and the provenance field labels -- so
    gutting the panel (emptying TRUST_STATES, deleting the fields, or dropping the
    render) turns this RED, not just renaming a declaration.
    """
    # The panel is actually RENDERED on every completed assistant turn.
    assert "{isAssistant && !streaming && <TrustPanel message={m} />}" in CHAT_UI

    # The four SERVED resolution statuses each map to a labeled state; deleting an
    # entry (or emptying TRUST_STATES) drops its label string -> RED.
    for label in ("Governed", "Governed + observed", "Observed only", "No governed match"):
        assert f'label: "{label}"' in CHAT_UI, f"trust state {label!r} removed"

    # observed_only and no_match are NOT governed-trusted, so each carries a NEXT ACTION
    # (an `action:`), not just a label -- pin the action sentences themselves.
    assert "Open Explore context or refine the question to pull in a governed bundle" in CHAT_UI
    assert (
        "Refine the question, or open Explore context to find a related governed domain" in CHAT_UI
    )

    # The immutable provenance fields render as first-class rows (not a JSON blob).
    for field in ("Bundle ID", "Git authority", "Agent", "Provider / model", "Policy"):
        assert f"<dt>{field}</dt>" in CHAT_UI, f"provenance field {field!r} removed"
    assert "authority.commit_sha" in CHAT_UI  # the Git authority commit, first-class
    assert "result?.agent_config?.policy_result" in CHAT_UI

    # A stale/conflict warning is called out with a reconcile next action, and a
    # timeout is its own labeled state with a retry action.
    assert "function isStaleOrConflict(" in CHAT_UI
    assert "re-sync the source or reconcile the conflict before relying on this" in CHAT_UI
    assert "function isTimeout(" in CHAT_UI
    assert "Timed out" in CHAT_UI
    assert "Retry the question, or narrow it and lower reasoning effort" in CHAT_UI

    # SECURITY (hy-icx1 #448): the warning message is server-side redacted at
    # schema.warning, and the render defends in depth -- it must go through the
    # canonical redactUserinfo, never interpolate warning.message raw, so a future
    # verbatim render (or deleting the helper) turns this RED.
    assert "function redactUserinfo(" in CHAT_UI
    assert "{redactUserinfo(warning.message)}" in CHAT_UI
    assert "> {warning.message}" not in CHAT_UI  # the pre-fix verbatim render is gone


def test_chat_free_text_is_redacted_at_the_data_boundary():
    """hy-6tsw9 #452: free-text is redacted at the DATA/PROPS boundary via `redactDeep`,
    one shape-independent choke point per component, so NO JSX render shape can leak a
    credential -- the class is closed by construction, not policed by a source regex.
    (The round-1 regex guard was BYPASSABLE -- it missed {message.content}, bare {error},
    {x || y}, spaced/nested JSON.stringify -- so it is replaced by these boundary pins +
    the DOM tests in playground/ui/src/chat_redaction.test.jsx.) MUTATION-LOAD-BEARING:
    removing a boundary call turns this RED.
    """
    assert "function redactDeep(" in CHAT_UI and "function redactUserinfo(" in CHAT_UI

    # Each component redacts its server data as it ENTERS render -- the choke points:
    assert "const m = redactDeep(message);" in CHAT_UI  # Message: the whole answer + fields
    assert "redactDeep(agents.find(" in CHAT_UI  # AgentControls: selected agent detail
    assert "setItems(redactDeep(flat))" in CHAT_UI  # AssetSearch: catalog items
    assert (
        "redactDeep(result?.context_resolution?.error" in CHAT_UI
    )  # GovernedBlocked: message/detail/recovery

    # The biggest surface the adversary flagged: the LLM ANSWER renders ONLY from the
    # redacted copy, never the raw prop.
    assert "<Markdown>{m.content}</Markdown>" in CHAT_UI
    assert "{message.content}" not in CHAT_UI


def test_product_shell_exposes_only_the_mvp_surfaces_and_archives_slop():
    """The product shell keeps the four usable MVP surfaces and hides diagnostics."""
    for label in ("Live chat", "Explore the Hive-Mind", "Review", "Settings"):
        assert label in PLAYGROUND_UI
    assert 'const DEBUG_TABS = [["chat", "Live chat"]];' in PLAYGROUND_UI
    assert "const ADMIN_TABS" not in PLAYGROUND_UI
    assert "function SettingsTabs" not in PLAYGROUND_UI
    assert "function adminTabFromPath()" in PLAYGROUND_UI
    assert 'return "readiness";' in PLAYGROUND_UI
    assert "userShell ? userSection : surface" in PLAYGROUND_UI
    assert '{surface !== "playground" && <SurfaceNav current={surface} />}' not in PLAYGROUND_UI
