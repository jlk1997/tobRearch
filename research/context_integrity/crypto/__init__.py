"""Re-export the in-repo authenticated-context defence for paper evals."""

from openlens_ai.utils.authenticated_context import (  # noqa: F401
    AuthenticatedContext,
    apply_ifc,
    flatten_overflow_preserving_auth,
    reconstruct_after_compression,
    wrap_sidecar,
)
