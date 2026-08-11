"""Cross-cutting concerns — the one layer every other layer may import.

`core` holds what has no natural home in domain, infrastructure, or the API
because it is used by all three: configuration, observability, and the
exception hierarchy the layers raise across their boundaries.

The rule that keeps this from becoming a junk drawer: nothing in `core` may
import from `app.domain`, `app.infrastructure`, `app.services`, or
`app.api`. It is a leaf. Anything that needs to know about a domain type
belongs in the layer that owns that type.
"""
