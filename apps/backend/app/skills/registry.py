"""``SkillRegistry`` — loads and caches all skills at FastAPI startup.

Mirrors docs/04 §20.5 + docs/05 §3.4: each ``apps/backend/skills/<name>/SKILL.md``
file is read once, split into YAML frontmatter + Markdown body via
``python-frontmatter``, validated against :class:`SkillManifest`, and
stored in an immutable in-memory dict keyed by skill name.

The registry is a singleton — the FastAPI lifespan in ``app.main`` calls
:meth:`SkillRegistry.bootstrap` exactly once on startup. Failure to
load or validate any skill aborts the process; we'd rather fail to
start than serve traffic with a half-broken skill set.

Per-brand override caching (Redis ``brand:{id}:skills:overrides``,
docs/04 §20.5 + docs/05 §3.4 / cache table) is **not** in this PR —
custom brand skills live on the ``brand_custom_skills`` table that
ships in v1.1 (docs/04 §20.6 Level 2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import frontmatter

from app.errors import SkillValidationFailedError
from app.skills.manifest import SkillManifest

# docs/05 §2.1 lays out the monorepo: backend code under
# ``apps/backend/app/`` and the skill blobs themselves at
# ``apps/backend/skills/<name>/SKILL.md``.
_APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR: Final[Path] = _APP_ROOT.parent / "skills"

SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class LoadedSkill:
    """Validated manifest + provenance metadata."""

    manifest: SkillManifest
    source_path: Path
    # docs/04 §20.7: "global" vs "custom" lineage. We keep this as a
    # short string because a future brand-custom skill (v1.1) just
    # flips this to ``"custom"`` and adds a ``brand_id`` (TODO).
    source: str = "global"

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def body(self) -> str:
        return self.manifest.body

    @property
    def tags(self) -> tuple[str, ...]:
        return self.manifest.tags


class SkillRegistry:
    """In-memory store of every validated global skill."""

    def __init__(self, skills: dict[str, LoadedSkill]) -> None:
        self._skills = dict(skills)

    @classmethod
    def load_all(cls, root: Path | str | None = None) -> SkillRegistry:
        """Sync constructor used by the lifespan + tests + CI linter."""

        root_path = Path(root) if root is not None else DEFAULT_SKILLS_DIR
        skills = _load_directory(root_path)
        return cls(skills)

    @classmethod
    async def bootstrap(cls, root: Path | str | None = None) -> SkillRegistry:
        """Async wrapper used by FastAPI's lifespan."""

        return cls.load_all(root)

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(sorted(self._skills.values(), key=_sort_key))

    def all(self) -> list[LoadedSkill]:
        """Deterministic ordering — safety/system first, then alpha."""

        return sorted(self._skills.values(), key=_sort_key)

    def get(self, name: str) -> LoadedSkill | None:
        return self._skills.get(name)

    def for_brand(self, _brand_id: uuid.UUID) -> list[LoadedSkill]:
        """Hook for v1.1 L2 brand_custom_skills.

        On MVP every brand sees the global registry verbatim; the
        Compiler filters out non-safety entries the brand has chosen to
        disable. The argument is kept so callers can be written once
        and not need a touch-up when L2 lands.
        """

        return self.all()


def _load_directory(root: Path) -> dict[str, LoadedSkill]:
    skills: dict[str, LoadedSkill] = {}
    if not root.exists():
        # Empty skill set is legal — useful for tests + early bootstrap.
        return skills
    if not root.is_dir():
        raise SkillValidationFailedError(f"Skill root {root} is not a directory")

    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        skill_md = skill_dir / SKILL_FILENAME
        if not skill_md.exists():
            # Directories without a SKILL.md are not skills; could be a
            # holder for supporting docs ("supporting_files" in §20.3).
            continue
        loaded = _load_one(skill_md)
        if loaded.name != skill_dir.name:
            raise SkillValidationFailedError(
                f"Skill name {loaded.name!r} in {skill_md} does not match its directory {skill_dir.name!r}",
            )
        if loaded.name in skills:
            raise SkillValidationFailedError(
                f"Duplicate skill name {loaded.name!r} ({skill_md} vs {skills[loaded.name].source_path})",
            )
        skills[loaded.name] = loaded
    return skills


def _load_one(path: Path) -> LoadedSkill:
    raw = path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(raw)
    except Exception as exc:
        raise SkillValidationFailedError(f"{path}: cannot parse YAML frontmatter: {exc}") from exc
    metadata = dict(post.metadata)
    metadata["body"] = post.content.strip()
    try:
        manifest = SkillManifest.model_validate(metadata)
    except SkillValidationFailedError:
        raise
    except Exception as exc:
        raise SkillValidationFailedError(f"{path}: {exc}") from exc
    return LoadedSkill(manifest=manifest, source_path=path, source="global")


def _sort_key(skill: LoadedSkill) -> tuple[int, str]:
    """Safety/system skills float to the front; rest alphabetical.

    The Compiler also sorts before splicing bodies together — keeping
    the same ordering for both calls makes the rendered prompt
    bit-stable across processes.
    """

    has_safety = bool(set(skill.tags) & {"safety", "system"})
    return (0 if has_safety else 1, skill.name)
