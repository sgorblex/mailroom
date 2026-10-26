"""Mailroom's built-in OAuth 2.1 authorization server for its MCP server.

Self-contained, so it can move to a shared package later: it depends on SQLAlchemy, the ``mcp``
SDK's authorization handlers, httpx and the app's ``user`` table, never on the app's routes. The
pieces: ``server.OAuthServer`` (routes and token verification), ``consent`` (the consent
screen's calls), ``clients`` (registration and client metadata documents), ``tokens``.
"""
