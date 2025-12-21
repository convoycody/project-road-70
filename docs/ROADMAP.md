# Roadmap (Grounded in existing rules)

## Now (stabilization)
- Verify iPhone Safari permissions (motion sensors + location) and Start/Stop gating.
- Verify 60s bucket aggregation and local confidence computation.
- Verify server ingest → storage → admin visibility.
- Verify geocode backfill + score recompute + event detection pipelines.
- Validate webhook deploy flow (signed payload, branch filter, restart).

## Next (planned, not implemented here)
- User rewards points system (requires login + persistent identity).
- User login to track points history and balances.
- Expanded metrics and scoring (while preserving privacy-first aggregate-only upload).
- Admin UI surfacing of road events and segment insights.

## Later (directional)
- Public map views and segment insights backed by rollups.
- Export/reporting endpoints for partners or municipal use.
- Configurable event thresholds driven by data quality.

Snapshot time (UTC): 2025-12-21T00:31:59Z
