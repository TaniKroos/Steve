"""
backend.app -- the FastAPI service the frontend talks to.

Layout (see .Arch/backend-service-lld.md for the full design writeup):
  config.py        settings, extending cloudagent_core's shared CoreSettings
  dependencies.py  every FastAPI Depends() provider -- the DI wiring, in one place
  main.py          FastAPI() app instance, middleware, router registration
  routers/         HTTP layer only -- parse request, call one service method, shape response
  services/        business logic -- the only layer allowed to make decisions
  repositories/    data access, one class per DB aggregate
  schemas/         Pydantic request/response DTOs -- the only shapes crossing the HTTP boundary
  clients/         adapters to things outside this process (Agent Loop, GitHub REST)
  events/          Redis subscribe -> SSE relay
"""
