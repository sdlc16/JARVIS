"""`jarvis docs`: administracion de la memoria desde la linea de comandos."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from openjarvis.cli import docs_cmd
from openjarvis.jarvis.knowledge import KnowledgeBase

pytest.importorskip("openjarvis._rust_bridge")


@pytest.fixture
def env(tmp_path, monkeypatch):
    docs = tmp_path / "Documentos"
    docs.mkdir()
    (docs / "nota.txt").write_text("El código de la taquilla del gimnasio es 4821.")
    try:
        kb_open = KnowledgeBase(tmp_path / "m.db", roots=[docs])
        kb_all = KnowledgeBase(tmp_path / "m.db", roots=None)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"memoria no disponible: {exc}")
    monkeypatch.setattr(
        docs_cmd, "_kb", lambda allow_all=False: kb_all if allow_all else kb_open
    )
    return docs, tmp_path


def run(*args, input=None):
    return CliRunner().invoke(docs_cmd.docs, list(args), input=input)


def test_index_search_list_remove_flow(env):
    docs, _ = env
    r = run("index", str(docs))
    assert r.exit_code == 0 and "1 indexado" in r.output
    assert "sin cambios" in run("index", str(docs)).output  # reindexar no duplica
    r = run("search", "taquilla", "gimnasio")
    assert r.exit_code == 0 and "4821" in r.output
    assert "1 documento(s)" in run("list").output
    r = run("remove", str(docs / "nota.txt"))
    assert "1 trozo" in r.output and (docs / "nota.txt").exists()  # el archivo sigue
    assert "Sin resultados" in run("search", "taquilla").output


def test_index_outside_allowed_roots_exits_with_error(env):
    _, tmp = env
    (tmp / "fuera.txt").write_text("secreto")
    r = run("index", str(tmp / "fuera.txt"))
    assert r.exit_code == 2 and "no permitida" in r.output.lower()
    assert run("index", str(tmp / "nada.txt")).exit_code in (1, 2)


def test_remember_facts_forget(env):
    out = run("remember", "mi", "gato", "se", "llama", "Miso").output
    doc_id = out.split("id ")[1].split(")")[0]
    assert "Miso" in run("facts").output and "Miso" in run("search", "gato").output
    assert "Olvidado" in run("forget", doc_id).output
    assert "No hay hechos" in run("facts").output
    assert "No existe" in run("forget", "inexistente").output


def test_clear_requires_confirmation(env):
    run("remember", "dato importante")
    assert "Cancelado" in run("clear", input="n\n").output
    assert "dato importante" in run("facts").output
    assert "borrada" in run("clear", "--yes").output
    assert "No hay hechos" in run("facts").output
