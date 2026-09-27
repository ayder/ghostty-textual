# Performance measurements

Measured on macOS 26.5.2 ARM64, Python 3.12.12, at 120×40. The baseline is
`eeaf9fa`; both versions ran the same benchmark scripts against the same installed
dependencies, sequentially. These are synthetic local measurements, not latency
guarantees or complete application frame times.

Extraction and rendering use 5 warmups and 60 measured samples. Feed benchmarks
use 5 warmups and 5 measured samples; their p95 is consequently the maximum of
those five samples. The terminal is reset outside the timed region for each feed
sample. Garbage collection remains enabled.

| Workload | Before median / p95 (ms) | After median / p95 (ms) |
|---|---:|---:|
| Plain full extraction | 5.498 / 8.220 | 5.690 / 8.475 |
| Styled full extraction | 13.162 / 15.672 | 8.303 / 8.655 |
| Alternating-color extraction | 13.181 / 15.697 | 8.351 / 8.872 |
| Linked full extraction | 13.070 / 15.718 | 9.784 / 12.741 |
| Single dirty row extraction | 0.198 / 0.216 | 0.198 / 0.207 |
| Cursor-only snapshot | 0.045 / 0.048 | 0.038 / 0.041 |
| Suppressed synchronized snapshot | 0.032 / 0.036 | <0.001 / 0.001 |
| Plain rendering | 0.508 / 0.535 | 0.383 / 0.438 |
| Plain selected rendering | 1.491 / 1.534 | 0.397 / 0.417 |
| Alternating-color rendering | 12.956 / 13.311 | 6.846 / 6.970 |
| Alternating-color selected rendering | 18.513 / 18.919 | 6.899 / 7.047 |
| Widget feed: 4 KiB, one-byte chunks | 866.520 / 877.751 | 13.329 / 13.522 |
| Widget feed: 4 KiB, 64-byte chunks | 19.968 / 20.305 | 7.005 / 7.422 |
| Widget feed: 4 KiB, one chunk | 7.002 / 12.145 | 6.994 / 7.316 |

The largest gain comes from doing one snapshot per burst. Headless callers that
explicitly snapshot after every byte still pay for every snapshot: that benchmark
remained approximately 820 ms. The widget feed benchmark ends after a loop turn
has executed deferred extraction; it excludes compositor work and terminal output.
Rendering benchmarks measure all 40 rows separately from extraction, on an already
populated shadow frame.

Plain extraction and single-row extraction are essentially unchanged. Fully
hyperlinked extraction still exceeds 10 ms at p95 on this run, and alternating
styles still require thousands of Rich segments. These remain useful workloads
for future profiling; the existing 10 ms regression gate covers plain extraction.

## Implementation

- `TerminalView.feed()` parses input and dispatches effects immediately, scheduling
  one cancellable callback for frame extraction on the next event-loop turn.
  Explicit `refresh_frame()` remains synchronous and consumes pending work. Reset,
  failure, and unmount cancel pending callbacks. There is no frame-rate cap.
- Rich styles, including cursor/selection overlays, use a 4096-entry LRU cache
  keyed by immutable `CellStyle` values and overlay kind. Keys do not retain widget
  or terminal objects, and reused interner IDs cannot alias older styles.
- Selection bounds and cursor placement are calculated once per rendered row.
- Native style conversion caches exact copies of public `GhosttyStyle` accessor
  results plus resolved foreground/background colors, up to 4096 entries. It still
  queries native styling per cell. The cache clears on every render-state update,
  including extraction retries, so palette/default changes cannot leave stale
  underline colors. It does not depend on native style ID lifetime. Struct padding
  differences can cause cache misses but cannot equate different field values.
- Hyperlink extraction reuses native point/reference and URI buffers. Short links
  need one URI accessor call; longer accepted links grow the buffer and retry.
  Reported lengths above the configured URI limit are rejected before allocating
  a larger buffer. Link identity caching is intentionally absent: adjacent linked
  cells may refer to different URLs.
- Suppressed snapshots check only mode 2026 and the hold deadline before touching
  render state. Forced snapshots bypass suppression. Actual snapshots still check
  cursor and palette changes and retain the existing dirty-acknowledgement rules.

## Reproduce

```bash
uv run python benchmarks/frame_extraction.py --samples 60 --fragment-samples 5
uv run python benchmarks/widget_rendering.py --samples 60
uv run python benchmarks/feed_bursts.py --samples 5
uv run pytest
uv run ruff check src tests benchmarks
```

Increase sample counts when using p95 for capacity planning. Functional regressions
cover snapshot counts, ordered effects, callback cancellation, forced and timed-out
synchronized output, selection/cursor overlays, palette changes, buffer growth,
and cached-versus-uncached native style extraction. Timing assertions are kept in
the existing performance tests.
