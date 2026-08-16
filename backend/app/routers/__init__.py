"""
HTTP layer. Every route function here does exactly three things: validate
input (Pydantic/FastAPI does most of this automatically from the type
hints), call one service method, shape the response. No SQL, no
SQLAlchemy imports, no business `if` statements -- if you're reaching for
one here, that logic belongs in services/ instead.
"""
