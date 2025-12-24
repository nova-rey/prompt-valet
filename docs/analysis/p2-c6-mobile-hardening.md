# Phase 2 · Checkpoint 6 Mobile Hardening & UX Polish

## Findings

### Job dashboard (prompt_valet/ui/app.py:562-705)
- The table renders seven columns at once with pagination disabled, so narrow phones either force a horizontal scroll trap or compress every cell, making each row a tiny tap target. Selection is triggered by tapping the table row itself (`jobs_table.on_select`), so there is no affordance for finger-friendly intent, and the refresh/sort buttons share the same header row without "touch spacing."
- Job IDs appear only as plain one-line labels, so long IDs clip on mobile and there is no copy affordance that would help recover them when the table wraps horizontally.

### Job detail dialog (prompt_valet/ui/app.py:523-645)
- The dialog uses a `max-w-4xl` card with the log pane hard-coded to `min-h-[220px]` and the SSE status label forced to `white-space: nowrap`, so the content overflows on small screens and the textarea can dead-end the main scroll (a scroll trap). Buttons for refresh/live/pause/abort stay tightly grouped and have no responsive width tweaks, which keeps tap targets small.
- Logs stream via `_run_live_stream` and `_stop_live_stream` but the lifecycle only reacts to explicit detail closes; there is no hook for page visibility/resume, so a running SSE stream can become stale when the user backgrounds the browser or navigates away and then returns. The status label is also only updated via `_set_sse_status`, so reconnection states look identical between a paused stream and a canceled one.
- Job metadata is dumped into a table that keeps every field on one row without wrap or copy affordances, so key/value pairs overflow sideways on phones.

### Submit panel (prompt_valet/ui/app.py:708-1019)
- The target select row uses `.items-end gap-4 flex-wrap` with each control capped to `sm:w-1/3`, but the adjacent "Reload targets" button is left in the same row; on small screens that still squeezes the button against the selects, reducing tap area and increasing mis-taps.
- Compose/upload forms rely on textareas, inputs, and buttons without additional padding or multi-line labels, so the main CTA buttons (`Submit text`, `Submit files`) are sandwiched in a horizontal row with no responsive stacking, again shrinking hit targets.
- Submission results render inline markdown links (`View job detail`) which, if copy/pasted, clip longer job IDs when the panel narrows.

### Services tab (prompt_valet/ui/app.py:1031-1265)
- Status badges use `text-xs` typography and are presented in `ui.row` grids that assume enough horizontal real estate; when cards stack on phones the badges remain text-heavy and there is no scroll-safe container or wrapping helper, so the badges stay tiny and the 4+ lines of status text create dense blocks.
- The connectivity hint label is updated with raw timestamp text, but there is no affordance that keeps it readable when the line wraps (no trimming or stacking headlined). The refresh button is just an icon-based pill, which becomes a harder tap target on small screens.

### Streaming lifecycle
- SSE streaming (`_run_live_stream`, `_stop_live_stream`, `_start_live_stream`, `_handle_live_button` at prompt_valet/ui/app.py:270-353) only disconnects when `_stop_live_stream` is explicitly invoked by UI events; there is no automatic cancellation on navigation/visibility changes, and `_start_live_stream` ignores any resume logic after the stream is aborted/loading, so the label can remain "Connecting…" or "Live logs paused" indefinitely when the page reconnects, leaving users unsure if they need to manually restart.

## Actionable checklist

1. Wrap the jobs table in a touch-safe container (overflow layer, descriptive row buttons, or card fallback) so rows do not shrink into precision-only tap targets and long job IDs can wrap or be copied rather than clipped.
2. Reflow the detail dialog: let the log card stretch to the viewport height with explicit `overflow-auto`, ensure refresh/live/pause/abort buttons stack or expand on narrow screens, and remove `white-space: nowrap` from the SSE label so status text can wrap naturally.
3. Harden the SSE lifecycle by disconnecting on dialog close, cancelling when the tab loses visibility, and reestablishing a clean stream/resolved status label when focus returns or the user reopens the detail view. Synchronize `_set_sse_status` text with each lifecycle transition so mobile users see the correct state.
4. Protect the metadata table/log textarea from horizontal overflow by enabling wrapping/preformatted spans and exposing copy controls for long values that would otherwise be clipped.
5. Adjust the submit panel controls so the repo/branch selects, reload button, and submit CTAs stack vertically on small screens with extra padding, ensuring each CTA is at least 44px in height; keep the result/link section wrapping gracefully so long job IDs do not overflow.
6. Improve the services tab layout by letting cards stack with tighter vertical spacing, increasing badge font size or padding on mobile, and adding more descriptive text above the refresh control so the pill-sized icon is easier to hit.
7. Consolidate the connectivity and status messages (dashboard header + services tab) into multi-line labels that reflow instead of clipping, making timestamps and health cues legible on small widths.

