"""Pure domain rules, with no dependency on the database or on HTTP.

Modules here are importable from `app.models`, `app.services` and `app.schemas`
alike without creating a cycle, which is what lets one definition of a rule
serve the CHECK constraint, the service that enforces it and the schema that
publishes it.
"""
