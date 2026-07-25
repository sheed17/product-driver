"""Path derivation and approved-root confinement.

Two responsibilities, both about *where* the driver is allowed to be:

1. **Derivation.** The Product Driver's own root is derived from the installed
   package or the working directory — never assumed to sit at a remembered
   absolute path. The target Neyma repository is always configured explicitly
   and must exist; there is deliberately no fallback, because a silent fallback
   to an obsolete path is how a run ends up reading a repository that is not the
   product.

2. **Confinement.** The builder runs with broad local freedom, so the boundary
   that matters is *which roots* it may write into. Every write is resolved to a
   real path — ``~`` expanded, ``../`` collapsed, symlinks followed — and
   refused unless it lands inside an approved root. Resolution happens through
   :func:`os.path.realpath`, so a symlink pointing out of an approved root is
   refused on the link's *target*, not on its innocent-looking name.

Approved roots are a configuration model, not a hardcoded list. The default set
is derived: the target repository, the driver's run-artifact directory, the
configured preservation directory, and the configured temporary workspace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

# The directory holding this package. Used to derive the driver root.
PACKAGE_DIR = Path(__file__).resolve().parent


class RepositoryPathError(ValueError):
    """A configured repository path is missing, or is not what it claims to be."""


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------


def expand(value: str | os.PathLike[str]) -> Path:
    """Expand ``~``/``$VAR`` and make absolute, without requiring existence.

    Symlinks are deliberately *not* resolved here — configuration should read
    back as the operator wrote it. Symlink resolution is a confinement concern
    and happens in :meth:`ApprovedRoots.classify_write`.
    """
    raw = os.path.expandvars(os.path.expanduser(str(value)))
    return Path(os.path.abspath(os.path.normpath(raw)))


def expand_resolved(value: str | os.PathLike[str]) -> Path:
    """Expand and fully resolve — the form configuration paths are stored in.

    Configuration paths are resolved so that two spellings of the same directory
    compare equal, which is what approved-root containment relies on.
    """
    return Path(os.path.realpath(_absolute(value)))


def looks_like_driver_root(path: Path) -> bool:
    """True when ``path`` is a Product Driver checkout or installation root."""
    return (path / "pyproject.toml").is_file() and (path / "neyma_product_driver").is_dir()


def discover_driver_root(start: Path | None = None) -> Path:
    """Derive the Product Driver root from where the package actually lives.

    Order, first match wins:

    1. the package's parent directory, when it is a real checkout (the editable
       install and the "run it from the repo" case — by far the common one);
    2. the working directory or any of its ancestors that looks like a checkout,
       which covers running an installed console script from inside the repo;
    3. the package's parent directory as a last resort.

    This never consults a remembered absolute path, and never raises: the
    fallback is always *somewhere the package actually is*.
    """
    package_parent = PACKAGE_DIR.parent
    if looks_like_driver_root(package_parent):
        return package_parent

    cwd = expand(start) if start is not None else Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if looks_like_driver_root(candidate):
            return candidate

    return package_parent


def resolve_target_repo(value: str | os.PathLike[str] | None) -> Path:
    """Resolve and validate the configured target repository path.

    Raises :class:`RepositoryPathError` with an actionable message rather than
    falling back to anything. A driver pointed at a path that no longer holds
    the product must stop, not quietly read the wrong tree.
    """
    if value is None or str(value).strip() == "":
        raise RepositoryPathError(
            "neyma_repo is not configured. Set it in driver.config.yaml or pass "
            "--repo <path>; the driver never guesses where the product repository is."
        )
    path = expand(value)
    if not path.exists():
        raise RepositoryPathError(
            f"Configured neyma_repo does not exist: {path}. "
            "Update driver.config.yaml (or --repo) to the current repository path."
        )
    if not path.is_dir():
        raise RepositoryPathError(f"Configured neyma_repo is not a directory: {path}")
    return path


# --------------------------------------------------------------------------
# Approved roots
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovedRoot:
    """One directory the builder is allowed to write into."""

    name: str
    path: Path
    purpose: str

    def contains(self, candidate: Path) -> bool:
        try:
            return candidate == self.path or candidate.is_relative_to(self.path)
        except ValueError:  # pragma: no cover - differing drives on Windows
            return False


@dataclass(frozen=True)
class PathVerdict:
    """The outcome of checking one path against the approved roots."""

    raw: str
    resolved: Path
    root: ApprovedRoot | None
    reason: str | None
    #: True when resolution moved the path to a different real location — a
    #: symlink or a ``../`` component was involved. Recorded either way, because
    #: an escape that lands *inside* an approved root is still worth seeing.
    redirected: bool = False

    @property
    def allowed(self) -> bool:
        return self.reason is None


def _absolute(raw: str | os.PathLike[str], cwd: Path | None = None) -> str:
    """Expand ``~``/``$VAR`` and anchor a relative path to ``cwd``. No resolution."""
    text = os.path.expandvars(os.path.expanduser(str(raw)))
    if os.path.isabs(text):
        return text
    base = Path(cwd).resolve() if cwd is not None else Path.cwd()
    return os.path.join(str(base), text)


def real_path(raw: str | os.PathLike[str], cwd: Path | None = None) -> Path:
    """Fully resolve ``raw`` for a confinement decision.

    ``~`` is expanded, a relative path is joined to ``cwd``, ``../`` components
    are collapsed, and every symlink is followed — including a symlink as the
    final component, and including one that is currently broken. ``realpath``
    resolves links *before* collapsing ``..``, which is the semantic the
    filesystem itself uses; doing it textually would let
    ``approved/link-to-elsewhere/../escape`` look contained when it is not.
    """
    return Path(os.path.realpath(_absolute(raw, cwd)))


def textual_path(raw: str | os.PathLike[str], cwd: Path | None = None) -> Path:
    """Where ``raw`` would land if no symlink existed. Used to spot redirection."""
    return Path(os.path.normpath(_absolute(raw, cwd)))


class ApprovedRoots:
    """The set of local roots the builder may write into.

    Reads are not confined — the builder legitimately reads system Python,
    installed packages and documentation. Secret *reads* stay blocked by the
    command guard's path patterns, which are a separate and stricter rule.
    """

    def __init__(self, roots: Sequence[ApprovedRoot]) -> None:
        seen: dict[Path, ApprovedRoot] = {}
        for root in roots:
            resolved = Path(os.path.realpath(str(root.path)))
            if resolved not in seen:
                seen[resolved] = ApprovedRoot(root.name, resolved, root.purpose)
        # Longest path first, so the most specific root is reported as the match.
        self.roots: tuple[ApprovedRoot, ...] = tuple(
            sorted(seen.values(), key=lambda r: len(str(r.path)), reverse=True)
        )

    def __bool__(self) -> bool:
        return bool(self.roots)

    def __iter__(self) -> Iterator[ApprovedRoot]:
        return iter(self.roots)

    def describe(self) -> list[str]:
        return [f"{r.name}: {r.path}  ({r.purpose})" for r in self.roots]

    def root_for(self, resolved: Path) -> ApprovedRoot | None:
        for root in self.roots:
            if root.contains(resolved):
                return root
        return None

    def classify_write(self, raw: str, cwd: Path | None = None) -> PathVerdict:
        """Decide whether a write to ``raw`` is inside an approved root."""
        resolved = real_path(raw, cwd)
        # Where the path would have landed with no symlink in the way. A
        # mismatch means resolution redirected us, which is worth recording even
        # when the destination turns out to be approved.
        redirected = textual_path(raw, cwd) != resolved

        if not self.roots:
            # No roots configured: confinement is not in force. Say so honestly
            # rather than pretending to enforce a boundary that does not exist.
            return PathVerdict(str(raw), resolved, None, None, redirected)

        root = self.root_for(resolved)
        if root is not None:
            return PathVerdict(str(raw), resolved, root, None, redirected)

        if redirected:
            reason = (
                f"write escapes every approved root through a symlink or '..' "
                f"component ({raw} -> {resolved})"
            )
        else:
            reason = f"write outside every approved root ({resolved})"
        return PathVerdict(str(raw), resolved, None, reason, redirected)


def default_roots(
    *,
    target_repo: Path,
    runs_dir: Path,
    preservation_dir: Path,
    temp_workspace_root: Path,
    driver_root: Path | None = None,
    extra: Sequence[Path] = (),
) -> ApprovedRoots:
    """Assemble the standard approved-root set.

    ``driver_root`` is included **only** for an explicit Driver-maintenance run.
    During an ordinary product run the driver's own source is not writable by
    the builder — only its run-artifact directory is.
    """
    roots = [
        ApprovedRoot("target_repo", expand(target_repo), "the Neyma product repository"),
        ApprovedRoot("runs_dir", expand(runs_dir), "driver run artifacts and evidence"),
        ApprovedRoot("preservation", expand(preservation_dir), "preservation refs and bundles"),
        ApprovedRoot("temp_workspace", expand(temp_workspace_root), "temporary probes and scratch"),
    ]
    if driver_root is not None:
        roots.append(
            ApprovedRoot("driver_root", expand(driver_root), "Product Driver maintenance")
        )
    roots.extend(
        ApprovedRoot(f"extra_{i}", expand(p), "explicitly configured additional root")
        for i, p in enumerate(extra)
    )
    return ApprovedRoots(roots)
