from __future__ import annotations

from domain.error_catalog import ERROR_DEFINITIONS, error_ref, flash_error_message, resolve_error_key


def test_error_catalog_codes_are_unique() -> None:
    codes = [int(item["code"]) for item in ERROR_DEFINITIONS.values()]
    assert len(codes) == len(set(codes))


def test_admin_backup_and_wipe_errors_resolve_to_catalog_refs() -> None:
    cases = {
        "ZIP de backup inválido.": "#ERR-8013",
        "O pacote ZIP não contém nenhum ficheiro JSON.": "#ERR-8014",
        "Carrega um ficheiro JSON ou cola o conteúdo JSON.": "#ERR-8012",
        "Password inválida ou utilizador sem perfil admin.": "#ERR-8021",
        "Frase de confirmação inválida. Escreve exatamente: LIMPAR BASE PRAGTICO": "#ERR-8023",
        "Tipo de backup de sistema não suportado.": "#ERR-8017",
    }
    for message, expected_ref in cases.items():
        key = resolve_error_key(message)
        assert key
        assert error_ref(key) == expected_ref
        assert flash_error_message(message).startswith(expected_ref)


def test_runtime_and_feedback_errors_resolve_to_catalog_refs() -> None:
    cases = {
        "Resposta não encontrada.": "#ERR-5034",
        "Não encontrei a pergunta original dessa resposta.": "#ERR-5035",
        "Define DATABASE_URL para arrancar a aplicação em Railway.": "#ERR-8061",
        "Instala `psycopg[binary]` e `pgvector` para usar o índice PostgreSQL.": "#ERR-8063",
    }
    for message, expected_ref in cases.items():
        key = resolve_error_key(message)
        assert key
        assert error_ref(key) == expected_ref
