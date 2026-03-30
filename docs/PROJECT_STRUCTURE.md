# Estrutura do Projeto

Esta é a organização recomendada do repositório depois da limpeza da árvore principal. A ideia é manter a raiz focada em arranque, deploy e ficheiros de projeto, e concentrar a lógica Python em pastas previsíveis.

## Visão geral

```text
app.py
blueprints/
core/
domain/
integrations/
storage/
templates/
static/
docs/
scripts/
sql/
tests/
knowledge/
data/
```

## Responsabilidades por pasta

- `app.py`
  Entry point Flask e composição dos serviços partilhados pela aplicação.

- `blueprints/`
  Camada web Flask: rotas, views e endpoints HTTP.

- `core/`
  Infraestrutura transversal da aplicação: segurança, validação, scheduler, estado partilhado e helpers de orquestração.

- `domain/`
  Regras de negócio: cálculo de custos, parsing documental, ações operacionais do chat e migração de dados.

- `integrations/`
  Integrações externas e adaptadores: providers LLM/embeddings, RAG, AIS, meteorologia, marés, ondulação, avisos locais e índice vetorial.

- `storage/`
  Backends de persistência e utilitários de storage (`local`, `postgres`, constantes e helpers de escalas/manobras).

- `templates/` e `static/`
  Frontend server-rendered com Jinja, CSS e JavaScript.

- `docs/`
  Documentação funcional, estrutural e de deploy.

- `scripts/`
  Scripts operacionais e administrativos.

- `sql/`
  Schemas SQL e bootstrap da base de dados.

- `tests/`
  Suite automatizada com `unittest`.

- `knowledge/`
  Base documental usada pelo RAG.

- `data/`
  Dados locais de runtime para desenvolvimento.

## Convenções

- Novas regras de negócio vão para `domain/`.
- Novas integrações com APIs ou providers vão para `integrations/`.
- Código utilitário transversal, segurança ou estado partilhado vai para `core/`.
- Rotas HTTP ficam em `blueprints/`.
- Evitar novos módulos soltos na raiz do repositório.
