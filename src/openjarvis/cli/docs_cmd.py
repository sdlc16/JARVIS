"""``jarvis docs`` -- memoria persistente y documentos locales (administrable).

Indexa carpetas permitidas, busca, lista, borra documentos de la base (nunca el
archivo del disco) y gestiona los hechos que JARVIS recuerda. Todo local.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from openjarvis.jarvis.knowledge import KnowledgeBase, PathNotAllowed, default_roots


def _kb(allow_all: bool = False) -> KnowledgeBase:
    """`allow_all`: sin restricción de carpetas (lectura/borrado de lo ya indexado)."""
    if allow_all:
        return KnowledgeBase(roots=None)
    caps = None
    try:
        from openjarvis.windows import BridgeClient

        caps = BridgeClient().capabilities()
    except Exception:  # noqa: BLE001 - sin puente se usan las carpetas de /mnt/c/Users
        caps = None
    return KnowledgeBase(roots=default_roots(caps))


@click.group()
def docs() -> None:
    """Memoria y documentos locales de JARVIS."""


@docs.command()
@click.argument("path", type=click.Path())
@click.option(
    "--allow-any-path",
    is_flag=True,
    help="Desactiva la restricción a Documentos/Escritorio/Descargas (bajo tu "
    "responsabilidad).",
)
def index(path: str, allow_any_path: bool) -> None:
    """Indexa un archivo o carpeta (reindexar sustituye; lo sin cambios se salta)."""
    console = Console()
    kb = _kb(allow_any_path)
    t0 = time.monotonic()
    try:
        rep = kb.index_path(Path(path))
    except PathNotAllowed as exc:
        console.print(f"[red]Ruta no permitida:[/red] {exc}")
        raise SystemExit(2) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    console.print(
        f"[green]{len(rep.indexed)} indexado(s)[/green] ({rep.chunks} trozos, "
        f"{rep.replaced} sustituidos), {len(rep.unchanged)} sin cambios, "
        f"{len(rep.skipped)} omitidos, en {time.monotonic() - t0:.1f}s."
    )
    for p, why in list(rep.skipped.items())[:20]:
        console.print(f"[dim]  omitido {p}: {why}[/dim]")


@docs.command()
@click.argument("query", nargs=-1, required=True)
@click.option("-k", "--top-k", default=5, type=int)
def search(query: tuple[str, ...], top_k: int) -> None:
    """Busca en documentos y hechos."""
    console = Console()
    hits = _kb(True).search(" ".join(query), top_k)
    if not hits:
        console.print("[yellow]Sin resultados.[/yellow]")
        return
    t = Table(title=f"Búsqueda: {' '.join(query)}")
    for col in ("#", "Tipo", "Puntuación", "Fuente", "Contenido"):
        t.add_column(col)
    for i, h in enumerate(hits, 1):
        t.add_row(
            str(i),
            h.kind,
            f"{h.score:.2f}",
            h.source,
            h.content[:160].replace("\n", " "),
        )
    console.print(t)


@docs.command("list")
def list_() -> None:
    """Lista lo indexado (documentos y hechos)."""
    console = Console()
    kb = _kb(True)
    t = Table(title="Memoria de JARVIS")
    for col in ("Tipo", "Trozos", "Indexado", "Fuente"):
        t.add_column(col)
    for s in kb.list_sources():
        when = datetime.fromtimestamp(s.indexed_at).strftime("%Y-%m-%d %H:%M")
        t.add_row(s.kind, str(s.chunks), when, s.source)
    console.print(t)
    st = kb.stats()
    console.print(
        f"[dim]{st['documents']} documento(s), {st['facts']} hecho(s), "
        f"{st['chunks']} trozos, {st['db_kb']} KB en {st['db_path']}[/dim]"
    )


@docs.command()
@click.argument("source")
def remove(source: str) -> None:
    """Quita un documento de la base (NO borra el archivo del disco)."""
    n = _kb(True).remove_source(source)
    click.echo(f"{n} trozo(s) eliminados de la base." if n else "No estaba indexado.")


@docs.command()
@click.argument("text", nargs=-1, required=True)
def remember(text: tuple[str, ...]) -> None:
    """Guarda un hecho para que JARVIS lo recuerde."""
    doc_id = _kb(True).remember(" ".join(text))
    click.echo(f"Recordado (id {doc_id}).")


@docs.command()
@click.argument("doc_id")
def forget(doc_id: str) -> None:
    """Olvida un hecho/trozo por id (ver `jarvis docs facts`)."""
    click.echo("Olvidado." if _kb(True).forget(doc_id) else "No existe ese id.")


@docs.command()
def facts() -> None:
    """Lista lo que JARVIS recuerda de ti."""
    rows = _kb(True).list_facts()
    if not rows:
        click.echo("No hay hechos guardados.")
    for doc_id, content in rows:
        click.echo(f"{doc_id}  {content}")


@docs.command()
@click.option("--yes", is_flag=True, help="Confirmar el borrado de TODA la memoria.")
def clear(yes: bool) -> None:
    """Borra TODA la memoria (documentos y hechos). Pide confirmación."""
    if not (yes or click.confirm("¿Borrar toda la memoria de JARVIS?")):
        click.echo("Cancelado.")
        return
    _kb(True).clear()
    click.echo("Memoria borrada.")
