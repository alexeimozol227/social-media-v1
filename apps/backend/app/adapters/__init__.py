"""External-service adapters.

Each adapter is a thin, dialect-agnostic wrapper around an SDK or
HTTP API used by the platform. The service layer talks to the
adapter Protocol, not the SDK directly, so unit tests can swap in a
mock implementation. docs/04-architecture.md §13.
"""
