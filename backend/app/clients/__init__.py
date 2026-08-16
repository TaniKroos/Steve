"""
Adapters to things outside this process: Agent Loop (our other service)
and GitHub's REST API. Each client exposes only the small, domain-shaped
method set its callers actually need -- not the entire surface of the
thing it wraps (Interface Segregation) -- so a service depending on
`AgentLoopClient` is never tempted to reach past it for something the
design didn't account for.
"""
