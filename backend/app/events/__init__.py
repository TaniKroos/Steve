"""Redis subscribe -> SSE relay. Agent Loop publishes progress events to
Redis independently of any backend request (see flow 05 in
.Arch/backend-class-map.html); everything in this folder only ever
subscribes, never publishes."""
