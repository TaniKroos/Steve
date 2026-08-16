"""
cloudagent_core -- the package shared between `backend` and (later)
`agent_loop`.

Nothing runs by importing this package itself; it's a library of:
  - settings.CoreSettings   -- env vars both services need (DB URL, GitHub App identity)
  - db.models               -- the SQLAlchemy schema (single source of truth)
  - db.session              -- async engine / session-factory helpers
  - github_app.GithubApp    -- GitHub App JWT signing + installation-token minting

See .Arch/backend-service-lld.md and Requirements/requirements.md (NFR-14)
for why this is a separate installable package instead of copy-pasted
code in each service.
"""
