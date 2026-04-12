"""Advanced file handling.

Supports code files, plain text, and archives (zip/tar).
All paths are validated against the configured approved directory.
"""

import shutil
import tarfile
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from telegram import Document

from src.config.settings import Settings
from src.security.validators import SecurityValidator


@dataclass
class ProcessedFile:
    """Result of processing an uploaded file."""

    type: str
    prompt: str
    metadata: Dict[str, object]


@dataclass
class CodebaseAnalysis:
    """High-level analysis of an extracted codebase."""

    languages: Dict[str, int]
    frameworks: List[str]
    entry_points: List[str]
    todo_count: int
    test_coverage: bool
    file_stats: Dict[str, int]


# Extensions treated as source code
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".cpp",
        ".c",
        ".h",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
        ".r",
        ".jl",
        ".lua",
        ".pl",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".vue",
        ".yaml",
        ".yml",
        ".json",
        ".xml",
        ".toml",
        ".ini",
        ".cfg",
        ".dockerfile",
        ".makefile",
        ".cmake",
    }
)

_LANGUAGE_MAP: Dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".java": "Java",
    ".cpp": "C++",
    ".c": "C",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".r": "R",
    ".jl": "Julia",
    ".lua": "Lua",
    ".pl": "Perl",
    ".sh": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".vue": "Vue",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".xml": "XML",
}

# Skip these directories when scanning code
_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", "__pycache__", ".git", "dist", "build", ".venv"}
)

# Max archive size on disk (100 MB)
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


class FileHandler:
    """Handle document uploads from Telegram users."""

    def __init__(self, config: Settings, security: SecurityValidator) -> None:
        self.config = config
        self.security = security
        self._tmp = Path("/tmp/claude_bot_files")
        self._tmp.mkdir(exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def handle_document_upload(
        self, document: Document, user_id: int, context: str = ""
    ) -> ProcessedFile:
        """Download *document* and return a Claude-ready ProcessedFile."""
        file_path = await self._download(document)
        try:
            ftype = self._detect_type(file_path)
            if ftype == "archive":
                return await self._process_archive(file_path, context)
            elif ftype == "code":
                return await self._process_code(file_path, context)
            elif ftype == "text":
                return await self._process_text(file_path, context)
            else:
                raise ValueError(f"Unsupported file type: {ftype}")
        finally:
            file_path.unlink(missing_ok=True)

    # ── Download ───────────────────────────────────────────────────────────────

    async def _download(self, document: Document) -> Path:
        tg_file = await document.get_file()
        name = document.file_name or f"file_{uuid.uuid4()}"
        dest = self._tmp / name
        await tg_file.download_to_drive(str(dest))
        return dest

    # ── Type detection ─────────────────────────────────────────────────────────

    def _detect_type(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext in {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z"}:
            return "archive"
        if ext in _CODE_EXTENSIONS:
            return "code"
        try:
            with open(path, "r", encoding="utf-8") as fh:
                fh.read(1024)
            return "text"
        except (UnicodeDecodeError, OSError):
            return "binary"

    # ── Processors ────────────────────────────────────────────────────────────

    async def _process_archive(self, archive: Path, ctx: str) -> ProcessedFile:
        extract_dir = self._tmp / f"extract_{uuid.uuid4()}"
        extract_dir.mkdir()
        try:
            self._extract_archive(archive, extract_dir)
            tree = self._build_tree(extract_dir)
            code_files = self._find_code_files(extract_dir)
            prompt = f"{ctx}\n\nProject structure:\n{tree}\n\n"
            for fp in code_files[:5]:
                content = fp.read_text(encoding="utf-8", errors="ignore")
                rel = fp.relative_to(extract_dir)
                prompt += f"\nFile: {rel}\n```\n{content[:1000]}...\n```\n"
            return ProcessedFile(
                type="archive",
                prompt=prompt,
                metadata={
                    "file_count": len(list(extract_dir.rglob("*"))),
                    "code_files": len(code_files),
                },
            )
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    def _extract_archive(self, archive: Path, dest: Path) -> None:
        ext = archive.suffix.lower()
        if ext == ".zip":
            with zipfile.ZipFile(archive) as zf:
                total = sum(f.file_size for f in zf.filelist)
                if total > _MAX_ARCHIVE_BYTES:
                    raise ValueError("Archive too large (> 100 MB)")
                for info in zf.filelist:
                    fp = Path(info.filename)
                    if fp.is_absolute() or ".." in fp.parts:
                        continue
                    target = dest / fp
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive) as tf:
                total = sum(m.size for m in tf.getmembers())
                if total > _MAX_ARCHIVE_BYTES:
                    raise ValueError("Archive too large (> 100 MB)")
                for member in tf.getmembers():
                    if member.issym() or member.islnk() or member.isdev():
                        raise ValueError("Archive contains unsafe member types")
                    target = (dest / member.name).resolve(strict=False)
                    if not target.is_relative_to(dest.resolve()):
                        raise ValueError("Archive contains unsafe paths")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)

    async def _process_code(self, path: Path, ctx: str) -> ProcessedFile:
        content = path.read_text(encoding="utf-8", errors="ignore")
        lang = _LANGUAGE_MAP.get(path.suffix.lower(), "text")
        prompt = f"{ctx}\n\nFile: {path.name}\nLanguage: {lang}\n\n```{lang.lower()}\n{content}\n```"
        return ProcessedFile(
            type="code",
            prompt=prompt,
            metadata={
                "language": lang,
                "lines": len(content.splitlines()),
                "size": path.stat().st_size,
            },
        )

    async def _process_text(self, path: Path, ctx: str) -> ProcessedFile:
        content = path.read_text(encoding="utf-8", errors="ignore")
        prompt = f"{ctx}\n\nFile: {path.name}\n\n{content}"
        return ProcessedFile(
            type="text",
            prompt=prompt,
            metadata={"lines": len(content.splitlines()), "size": path.stat().st_size},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_tree(self, directory: Path, prefix: str = "") -> str:
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        lines: List[str] = []
        for i, item in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            if item.is_dir():
                lines.append(f"{prefix}{branch}{item.name}/")
                sub = prefix + ("    " if last else "│   ")
                lines.append(self._build_tree(item, sub))
            else:
                size = self._fmt_size(item.stat().st_size)
                lines.append(f"{prefix}{branch}{item.name} ({size})")
        return "\n".join(filter(None, lines))

    def _fmt_size(self, size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size //= 1024
        return f"{size:.1f}TB"

    def _find_code_files(self, directory: Path) -> List[Path]:
        files: List[Path] = []
        for fp in directory.rglob("*"):
            if not fp.is_file():
                continue
            if any(part in _SKIP_DIRS for part in fp.parts):
                continue
            if fp.suffix.lower() in _CODE_EXTENSIONS:
                files.append(fp)

        def _key(p: Path) -> tuple:
            name = p.name.lower()
            if name in {
                "main.py",
                "index.js",
                "app.py",
                "server.py",
                "main.go",
                "main.rs",
            }:
                return (0, name)
            if name.startswith("index."):
                return (1, name)
            if name.startswith("main."):
                return (2, name)
            return (3, name)

        files.sort(key=_key)
        return files

    async def analyze_codebase(self, directory: Path) -> CodebaseAnalysis:
        """Full codebase analysis."""
        lang_stats: Dict[str, int] = defaultdict(int)
        ext_stats: Dict[str, int] = defaultdict(int)
        for fp in directory.rglob("*"):
            if fp.is_file():
                ext = fp.suffix.lower()
                ext_stats[ext] += 1
                lang = _LANGUAGE_MAP.get(ext)
                if lang:
                    lang_stats[lang] += 1
        return CodebaseAnalysis(
            languages=dict(lang_stats),
            frameworks=self._detect_frameworks(directory),
            entry_points=self._find_entry_points(directory),
            todo_count=await self._count_todos(directory),
            test_coverage=bool(self._find_test_files(directory)),
            file_stats=dict(ext_stats),
        )

    def _find_entry_points(self, directory: Path) -> List[str]:
        patterns = [
            "main.py",
            "app.py",
            "server.py",
            "__main__.py",
            "index.js",
            "app.js",
            "server.js",
            "main.js",
            "main.go",
            "main.rs",
            "main.cpp",
            "main.c",
            "Main.java",
            "App.java",
            "index.php",
            "index.html",
        ]
        found: List[str] = []
        for p in patterns:
            for fp in directory.rglob(p):
                if fp.is_file():
                    found.append(str(fp.relative_to(directory)))
        return found

    def _detect_frameworks(self, directory: Path) -> List[str]:
        indicators: Dict[str, List[str]] = {
            "package.json": ["React", "Vue", "Angular", "Express", "Next.js"],
            "requirements.txt": ["Django", "Flask", "FastAPI", "PyTorch", "TensorFlow"],
            "Cargo.toml": ["Tokio", "Actix", "Rocket"],
            "go.mod": ["Gin", "Echo", "Fiber"],
            "pom.xml": ["Spring", "Maven"],
        }
        found: List[str] = []
        for fname, possible in indicators.items():
            fp = directory / fname
            if fp.exists():
                content = fp.read_text(encoding="utf-8", errors="ignore").lower()
                for fw in possible:
                    if fw.lower() in content:
                        found.append(fw)
        return list(set(found))

    async def _count_todos(self, directory: Path) -> int:
        count = 0
        for fp in directory.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in _CODE_EXTENSIONS:
                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore").upper()
                    count += text.count("TODO") + text.count("FIXME")
                except Exception:
                    continue
        return count

    def _find_test_files(self, directory: Path) -> List[Path]:
        patterns = ["test_*.py", "*_test.py", "*_test.go", "*.test.js", "*.spec.js"]
        result: List[Path] = []
        for p in patterns:
            result.extend(directory.rglob(p))
        return [f for f in result if f.is_file()]
