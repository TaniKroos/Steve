"""
Business logic layer -- the only layer allowed to make decisions
("does this user own this repo?", "in what order do we mint a token,
provision a sandbox, and call Agent Loop?"). Routers call exactly one
service method per endpoint; services call repositories and clients, never
the other way around.

Every service takes its collaborators (repositories, clients, other
services) via its constructor -- see dependencies.py for how FastAPI
builds the whole graph per request. This is what Dependency Inversion
looks like in practice here: a service like SessionService never imports
`asyncpg` or `httpx` directly, only the repository/client *types* it was
handed.
"""
