"""Auto-recover from a stale `index.html` referencing missing JS chunks.

Streamlit's frontend lazy-loads chunks like `StreamlitSyntaxHighlighter.<hash>.js`
the first time a page renders code, JSON, or a traceback. If a visitor's browser
is holding an `index.html` from a previous deploy whose chunk hashes no longer
exist on the server, that lazy-load surfaces as:

    TypeError: Failed to fetch dynamically imported module:
    https://<host>/static/js/StreamlitSyntaxHighlighter.<hash>.js

The Dockerfile pins Streamlit so chunk hashes are stable across deploys with the
same version — but bumping `streamlit==` (or any deploy that lands while a user
has a tab open from before the pin) still leaves that tab pointed at a chunk
URL the new image doesn't serve. This module installs a one-shot error handler
that detects that exact failure and force-reloads the tab so users self-heal
without knowing to hard-refresh.

Implementation note: this used to call `streamlit.components.v1.html`, which
Streamlit deprecated 2026-04 with removal after 2026-06-01 (and which was
firing the deprecation warning on every rerun, flooding the deploy logs).
The replacement is `st.html`, which renders inline in the main document
context rather than in a sandboxed iframe — fine for our use case since
the handlers we install attach to `window.top` and persist regardless of
how the inline element is rendered.
"""

import streamlit as st

_SCRIPT = """
<script>
(function () {
    try {
        var top = window.top;
        if (top.__autonomyChunkRecoveryInstalled) return;
        top.__autonomyChunkRecoveryInstalled = true;

        var PATTERN = /Failed to fetch dynamically imported module/i;
        var FLAG = '__autonomyChunkReloaded';

        function recover() {
            // sessionStorage flag prevents a reload loop if the new index.html
            // is also broken for some other reason.
            try {
                if (top.sessionStorage.getItem(FLAG) === '1') return;
                top.sessionStorage.setItem(FLAG, '1');
            } catch (_) {}
            top.location.reload();
        }

        function matches(value) {
            if (!value) return false;
            var msg = value.message || value;
            return PATTERN.test(String(msg));
        }

        top.addEventListener('error', function (e) {
            if (matches(e.error || e.message)) recover();
        }, true);
        top.addEventListener('unhandledrejection', function (e) {
            if (matches(e.reason)) recover();
        });
    } catch (_) {
        // Cross-origin sandbox blocks top access — silently no-op.
    }
})();
</script>
"""


_SESSION_FLAG = "_chunk_recovery_installed"


def install() -> None:
    """Inject the stale-chunk recovery script. Idempotent across reruns.

    Streamlit reruns the script on every interaction, so an unguarded call
    here would re-render the snippet every keystroke and (at minimum) spam
    the deploy logs. Gate on session_state so the snippet renders at most
    once per visitor session — the JS-side `__autonomyChunkRecoveryInstalled`
    flag handles the unlikely case where the gate fails (e.g. session_state
    cleared mid-session).
    """
    if st.session_state.get(_SESSION_FLAG):
        return
    st.html(_SCRIPT)
    st.session_state[_SESSION_FLAG] = True
