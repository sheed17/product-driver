"""Local product-driver for Neyma.

Drives a Claude Code builder session inside the Neyma repository, operates the
running product, judges the observed behaviour, and sends corrections back to
the same session until the result is accepted, blocked, or needs a product
decision from the owner.
"""

__version__ = "0.1.0"

from .models import Decision, EvaluatorDecision, RunState, RunStatus

__all__ = ["Decision", "EvaluatorDecision", "RunState", "RunStatus", "__version__"]
