"""Base de conocimiento: backend REAL (SQLiteMemory FTS5) sobre archivos reales."""

from __future__ import annotations

import os

import pytest

from openjarvis.jarvis.knowledge import (
    FACT_SOURCE,
    KnowledgeBase,
    PathNotAllowed,
    _win_to_wsl,
    clean_query,
    default_roots,
)

pytest.importorskip("openjarvis._rust_bridge")

CONTRATO = (
    "Contrato de alquiler del piso de Calle Mayor 12. La renta mensual es de 850 "
    "euros.\nEl vencimiento del contrato es el 31 de agosto de 2027."
)
PAELLA = "Paella valenciana: arroz bomba, pollo, conejo, azafrán y aceite de oliva."


@pytest.fixture
def kb(tmp_path):
    docs = tmp_path / "Documentos"
    docs.mkdir()
    (docs / "contrato.txt").write_text(CONTRATO, encoding="utf-8")
    (docs / "receta.md").write_text(PAELLA, encoding="utf-8")
    try:
        base = KnowledgeBase(tmp_path / "mem.db", roots=[docs])
    except Exception as exc:  # noqa: BLE001 - extension Rust ausente
        pytest.skip(f"backend de memoria no disponible: {exc}")
    base.docs = docs
    return base


def test_index_and_search_in_spanish_ignoring_accents(kb):
    rep = kb.index_path(kb.docs)
    assert sorted(p.split(os.sep)[-1] for p in rep.indexed) == [
        "contrato.txt",
        "receta.md",
    ]
    assert rep.chunks == 2 and not rep.skipped  # documentos cortos NO se descartan
    top = kb.search("cuanto es la renta del alquiler")[0]
    assert top.source.endswith("contrato.txt") and "850" in top.content
    assert kb.search("ingredientes arroz azafran")[0].source.endswith("receta.md")
    assert kb.search("") == [] and kb.search("zzzqqq inexistente") == []


def test_reindexing_unchanged_files_does_nothing_and_never_duplicates(kb):
    kb.index_path(kb.docs)
    rep = kb.index_path(kb.docs)
    assert not rep.indexed and len(rep.unchanged) == 2 and rep.chunks == 0
    assert kb.stats()["chunks"] == 2


def test_changed_file_replaces_its_old_content(kb):
    kb.index_path(kb.docs)
    (kb.docs / "contrato.txt").write_text("La renta mensual ahora es de 900 euros.")
    rep = kb.index_path(kb.docs / "contrato.txt")
    assert rep.replaced == 1 and rep.chunks == 1
    texts = " ".join(h.content for h in kb.search("renta mensual"))
    assert "900" in texts and "850" not in texts  # nada obsoleto
    assert kb.stats()["chunks"] == 2


def test_large_documents_are_split_and_every_part_is_findable(kb):
    body = "\n\n".join(f"Sección {i}: tema_{i} " + "relleno " * 120 for i in range(12))
    big = kb.docs / "manual.txt"
    big.write_text(body, encoding="utf-8")
    rep = kb.index_path(big)
    assert rep.chunks >= 3
    assert kb.search("tema_11")[0].source.endswith("manual.txt")


def test_paths_outside_the_allowed_roots_are_refused(kb, tmp_path):
    outside = tmp_path / "secreto.txt"
    outside.write_text("contenido privado")
    with pytest.raises(PathNotAllowed):
        kb.index_path(outside)
    with pytest.raises(PathNotAllowed):
        kb.index_path(kb.docs / ".." / "secreto.txt")  # escape con ..
    link = kb.docs / "enlace.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("sin permiso para symlinks")
    with pytest.raises(PathNotAllowed):
        kb.index_path(link)  # escape por enlace simbolico
    assert kb.stats()["chunks"] == 0


def test_sensitive_binary_hidden_huge_and_empty_files_are_skipped(kb):
    (kb.docs / "credentials.json").write_text('{"token": "abc"}')
    (kb.docs / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----")
    (kb.docs / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    (kb.docs / "vacio.txt").write_text("")
    (kb.docs / ".oculto.txt").write_text("no debe entrar")
    (kb.docs / "enorme.txt").write_text("x " * (3 * 1024 * 1024))
    (kb.docs / "datos.bin").write_bytes(b"abc\x00def")
    rep = kb.index_path(kb.docs)
    names = {p.split(os.sep)[-1]: why for p, why in rep.skipped.items()}
    assert "sensible" in names["credentials.json"] and "sensible" in names["id_rsa"]
    assert names["foto.png"] == "binario" and "grande" in names["enorme.txt"]
    assert names["vacio.txt"] == "vacío" and names["datos.bin"] == "binario"
    assert ".oculto.txt" not in names
    assert not kb.search("PRIVATE KEY") and not kb.search("token abc")


def test_facts_are_remembered_once_listed_and_forgotten(kb):
    a = kb.remember("Mi médico es la doctora Pérez, teléfono 600123456")
    assert kb.remember("  Mi médico es la doctora   Pérez, teléfono 600123456 ") == a
    kb.remember("Mi coche es un Seat León gris")
    assert [t for _i, t in kb.list_facts()][1].startswith("Mi médico")
    hit = kb.search("telefono de la doctora")[0]
    assert hit.kind == "fact" and hit.source == FACT_SOURCE and hit.id == a
    assert kb.forget(a) and not kb.search("doctora Pérez")
    assert kb.stats()["facts"] == 1
    with pytest.raises(ValueError):
        kb.remember("   ")


def test_memory_persists_across_instances_like_a_new_session(kb, tmp_path):
    kb.index_path(kb.docs)
    kb.remember("Mi contraseña del wifi NO se guarda, pero mi gato se llama Miso")
    again = KnowledgeBase(tmp_path / "mem.db", roots=[kb.docs])  # proceso nuevo
    assert again.search("gato Miso")[0].kind == "fact"
    assert again.search("renta alquiler")[0].source.endswith("contrato.txt")
    assert again.stats()["documents"] == 2


def test_list_remove_source_and_clear(kb):
    kb.index_path(kb.docs)
    kb.remember("un hecho cualquiera")
    srcs = {s.source.split(os.sep)[-1]: s for s in kb.list_sources()}
    assert srcs["contrato.txt"].kind == "document" and srcs[FACT_SOURCE].kind == "fact"
    assert kb.remove_source(str(kb.docs / "contrato.txt")) == 1
    assert not kb.search("renta mensual") and (kb.docs / "contrato.txt").exists()
    kb.clear()
    assert kb.stats()["chunks"] == 0


def test_missing_path_and_windows_path_mapping(kb):
    with pytest.raises(FileNotFoundError):
        kb.index_path(kb.docs / "no_existe.txt")
    assert str(_win_to_wsl(r"C:\Users\PC\Documents")) == "/mnt/c/Users/PC/Documents"
    assert _win_to_wsl("/home/x") is None
    caps = {"read_roots": [r"C:\Users\PC\Documents", r"D:\datos"]}
    assert [str(p) for p in default_roots(caps)] == [
        "/mnt/c/Users/PC/Documents",
        "/mnt/d/datos",
    ]


def test_clean_query_drops_stopwords_accents_and_signs():
    assert clean_query("¿Cómo se llama mi gato?") == "llama gato"
    assert clean_query("¿Cuánto es la renta del alquiler?") == "renta alquiler"
    assert (
        clean_query("de la el") == "de la el"
    )  # solo vacias: no se queda sin consulta


def test_natural_questions_do_not_return_noise(kb):
    kb.index_path(kb.docs)
    kb.remember("Mi gato se llama Miso")
    hits = [h for h in kb.search("¿Cómo se llama mi gato?") if h.score > 0]
    assert [h.kind for h in hits] == ["fact"]  # sin documentos irrelevantes
