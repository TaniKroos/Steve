"""
Pydantic DTOs (Data Transfer Objects) -- the only shapes allowed to cross
the HTTP boundary in either direction.

Why not just return the SQLAlchemy ORM models directly from a route?
Two reasons: (1) an ORM model might carry fields we never want a client
to see (nothing sensitive here yet, but it's a habit worth having from
the start), and (2) it decouples "what our database schema looks like"
from "what our API contract looks like" -- we can rename a DB column
without breaking the API response shape, or vice versa.
"""
