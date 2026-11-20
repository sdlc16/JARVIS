"""Base de conocimiento local de JARVIS: documentos indexados + hechos recordados.

Reutiliza el backend de memoria de OpenJarvis (``SQLiteMemory``, FTS5 con BM25 en
Rust) y añade lo que faltaba para administrarlo: reindexar sin duplicar,
saltar archivos sin cambios, validar rutas, listar y borrar por fuente, y
distinguir *documentos* de *hechos* que el usuario pide recordar. Todo local; la
misma base la consultan el agente (``memory_search``), la CLI y la GUI.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjarvis.tools.storage.chunking import ChunkConfig, chunk_text
from openjarvis.tools.storage.ingest import _should_skip_dir, read_document

FACT_SOURCE = "memoria:usuario"
MAX_FILE_BYTES = 5 * 1024 * 1024
_BINARY_EXT = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".mp3", ".mp4", ".wav",
        ".avi", ".mov", ".zip", ".tar", ".gz", ".bz2", ".7z", ".exe", ".dll",
        ".so", ".dylib", ".o", ".pyc", ".pyo", ".class", ".wasm", ".iso", ".msi",
    }
)  # fmt: skip


_STOPWORDS = frozenset(
    """a al algo ante con como cual cuales cuando cuanto cuanta cuantos cuantas de del
    donde el ella ellos en entre era es esta este esto estas estos fue ha han hay he la
    las le les lo los me mi mis mucho muy no nos o otro para pero por porque que quien
    se segun ser si sin sobre su sus te tengo tiene tienen tu tus un una uno unas unos
    y ya yo dime dame dice dijo puedes puedo quiero quieres sabes sabe recuerdas
    recuerdo""".split()
)  # fmt: skip


def clean_query(text: str) -> str:
    """Quita palabras vacías y signos: el backend busca con OR y 'mi'/'se'/'de'
    traían cualquier documento (ruido) para una pregunta en lenguaje natural."""
    import re
    import unicodedata

    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    words = [w for w in re.findall(r"[a-z0-9]+", plain) if len(w) > 1]
    keep = [w for w in words if w not in _STOPWORDS]
    return " ".join(keep or words)


class PathNotAllowed(PermissionError):
    """La ruta queda fuera de las carpetas permitidas para indexar."""


@dataclass
class IndexReport:
    indexed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # ruta -> motivo
    chunks: int = 0
    replaced: int = 0  # trozos antiguos sustituidos al reindexar


@dataclass
class Hit:
    id: str
    source: str
    content: str
    score: float
    kind: str  # document | fact


@dataclass
class SourceInfo:
    source: str
    kind: str
    chunks: int
    indexed_at: float


def default_roots(bridge_caps: dict[str, Any] | None = None) -> list[Path]:
    """Carpetas donde se permite indexar (mínimo privilegio).

    Preferencia: las raíces de lectura que declara el puente de Windows
    (Escritorio/Documentos/Descargas) convertidas a rutas WSL. Sin puente:
    esas mismas carpetas de cada usuario de ``/mnt/c/Users``.
    """
    roots: list[Path] = []
    for win in (bridge_caps or {}).get("read_roots", []):
        p = _win_to_wsl(str(win))
        if p is not None:
            roots.append(p)
    if not roots:
        users = Path("/mnt/c/Users")
        if users.is_dir():
            for u in users.iterdir():
                for sub in ("Documents", "Desktop", "Downloads"):
                    if (u / sub).is_dir():
                        roots.append(u / sub)
    return roots


def _win_to_wsl(path: str) -> Path | None:
    if len(path) >= 3 and path[1] == ":" and path[2] in "\\/":
        return Path("/mnt") / path[0].lower() / path[3:].replace("\\", "/")
    return None


class KnowledgeBase:
    def __init__(
        self,
        db_path: str | Path = "",
        roots: list[Path] | None = None,
        backend: Any = None,
    ) -> None:
        if not db_path:
            from openjarvis.core.config import load_config

            db_path = load_config().memory.db_path
        self.db_path = str(Path(str(db_path)).expanduser())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if backend is None:
            from openjarvis.tools.storage.sqlite import SQLiteMemory

            backend = SQLiteMemory(self.db_path)
        self._mem = backend
        self.roots = [r.resolve() for r in roots] if roots is not None else None

    # ------------------------------------------------------------------
    def _sql(self, query: str, args: tuple = ()) -> list[tuple]:
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(query, args).fetchall()
        finally:
            con.close()

    def _chunks_of(self, source: str) -> list[tuple[str, dict[str, Any]]]:
        rows = self._sql(
            "SELECT id, metadata FROM documents WHERE source = ?", (source,)
        )
        out = []
        for doc_id, meta in rows:
            try:
                out.append((doc_id, json.loads(meta) if meta else {}))
            except (TypeError, ValueError):
                out.append((doc_id, {}))
        return out

    def _check_allowed(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()  # resuelve enlaces simbolicos y ..
        if self.roots is None:
            return resolved
        for root in self.roots:
            if resolved == root or root in resolved.parents:
                return resolved
        raise PathNotAllowed(
            f"{resolved} está fuera de las carpetas permitidas: "
            + ", ".join(str(r) for r in self.roots)
        )

    # ------------------------------------------------------------------
    def index_path(
        self, path: str | Path, chunk: ChunkConfig | None = None
    ) -> IndexReport:
        """Indexa un archivo o carpeta. Reindexar sustituye, nunca duplica."""
        target = self._check_allowed(Path(path))
        if not target.exists():
            raise FileNotFoundError(f"No existe: {target}")
        cfg = chunk or ChunkConfig(chunk_size=512, chunk_overlap=64, min_chunk_size=1)
        report = IndexReport()
        files = [target] if target.is_file() else self._walk(target)
        for f in files:
            self._index_file(f, cfg, report)
        return report

    def _walk(self, folder: Path) -> list[Path]:
        out = []
        for child in sorted(folder.rglob("*")):
            if child.is_dir():
                continue
            rel = child.relative_to(folder)
            if any(_should_skip_dir(p) for p in rel.parts[:-1]):
                continue
            out.append(child)
        return out

    def _index_file(self, f: Path, cfg: ChunkConfig, report: IndexReport) -> None:
        from openjarvis.security.file_policy import is_sensitive_file

        name = str(f)
        if f.name.startswith("."):
            return
        if is_sensitive_file(f):
            report.skipped[name] = "archivo sensible (credenciales/claves)"
            return
        if f.suffix.lower() in _BINARY_EXT:
            report.skipped[name] = "binario"
            return
        try:
            size = f.stat().st_size
        except OSError as exc:
            report.skipped[name] = f"ilegible: {exc}"
            return
        if size > MAX_FILE_BYTES:
            report.skipped[name] = f"demasiado grande ({size // 1024} KB)"
            return
        try:
            text, _meta = read_document(f)
        except ImportError:
            report.skipped[name] = "falta pdfplumber para leer PDF"
            return
        except OSError as exc:
            report.skipped[name] = f"ilegible: {exc}"
            return
        if "\x00" in text[:2048]:
            report.skipped[name] = "binario"
            return
        sha = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        old = self._chunks_of(name)
        if old and all(m.get("sha256") == sha for _i, m in old):
            report.unchanged.append(name)
            return
        chunks = chunk_text(text, source=name, config=cfg)
        if not chunks:
            report.skipped[name] = "vacío"
            return
        for doc_id, _m in old:  # reindexar = sustituir
            self._mem.delete(doc_id)
        report.replaced += len(old)
        now = time.time()
        for c in chunks:
            self._mem.store(
                c.content,
                source=name,
                metadata={
                    "kind": "document",
                    "sha256": sha,
                    "index": c.index,
                    "offset": c.offset,
                    "indexed_at": now,
                },
            )
        report.indexed.append(name)
        report.chunks += len(chunks)

    # ------------------------------------------------------------------
    def remember(self, text: str) -> str:
        """Guarda un hecho que el usuario pide recordar (idempotente)."""
        text = " ".join(text.split())
        if not text:
            raise ValueError("No hay nada que recordar.")
        for doc_id, content in self._sql(
            "SELECT id, content FROM documents WHERE source = ?", (FACT_SOURCE,)
        ):
            if content == text:
                return doc_id
        return self._mem.store(
            text,
            source=FACT_SOURCE,
            metadata={"kind": "fact", "indexed_at": time.time()},
        )

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        hits = []
        for r in self._mem.retrieve(clean_query(query), top_k=top_k):
            kind = "fact" if r.source == FACT_SOURCE else "document"
            hits.append(
                Hit(r.metadata.get("id", "") or "", r.source, r.content, r.score, kind)
            )
        # el backend no devuelve el id: se resuelve por contenido exacto
        for h in hits:
            rows = self._sql(
                "SELECT id FROM documents WHERE source = ? AND content = ? LIMIT 1",
                (h.source, h.content),
            )
            if rows:
                h.id = rows[0][0]
        return hits

    def list_sources(self) -> list[SourceInfo]:
        rows = self._sql(
            "SELECT source, COUNT(*), MAX(created_at) FROM documents "
            "GROUP BY source ORDER BY MAX(created_at) DESC"
        )
        return [
            SourceInfo(
                s, "fact" if s == FACT_SOURCE else "document", int(n), float(ts or 0)
            )
            for s, n, ts in rows
        ]

    def list_facts(self) -> list[tuple[str, str]]:
        return [
            (i, c)
            for i, c in self._sql(
                "SELECT id, content FROM documents WHERE source = ? "
                "ORDER BY created_at DESC",
                (FACT_SOURCE,),
            )
        ]

    def forget(self, doc_id: str) -> bool:
        """Borra UN hecho/trozo por id."""
        return bool(self._mem.delete(doc_id))

    def remove_source(self, source: str) -> int:
        """Borra de la base todos los trozos de un documento (no toca el archivo)."""
        ids = [i for i, _m in self._chunks_of(str(source))]
        for doc_id in ids:
            self._mem.delete(doc_id)
        return len(ids)

    def clear(self) -> None:
        self._mem.clear()

    def stats(self) -> dict[str, Any]:
        srcs = self.list_sources()
        docs = [s for s in srcs if s.kind == "document"]
        return {
            "chunks": int(self._mem.count()),
            "documents": len(docs),
            "facts": sum(s.chunks for s in srcs if s.kind == "fact"),
            "db_path": self.db_path,
            "db_kb": round(Path(self.db_path).stat().st_size / 1024, 1)
            if Path(self.db_path).exists()
            else 0.0,
        }
