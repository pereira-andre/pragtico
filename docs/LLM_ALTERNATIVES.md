# Alternativas ao Gemini para o PRAGtico — Investigação Março 2026

Nota: este documento é uma nota exploratória de março de 2026. Os preços e catálogos de modelos devem ser reconfirmados antes de qualquer decisão de compra. As referências ao código abaixo foram atualizadas para refletir a estrutura atual do projeto.

## 1. Problema Atual

O PRAGtico usa a API do **Gemini** (modelo `gemini-2.5-flash`) para:
- **Geração de respostas** (chatbot RAG)
- **Embeddings** (indexação semântica da base de conhecimento)
- **Interpretação de comandos operacionais** (ações do bot)

Os limites do free tier do Gemini são restritivos:
- 5–15 RPM (requests per minute)
- 100–1000 requests/dia
- Quotas de embedding diárias baixas

Com utilização real por múltiplos utilizadores, estes limites esgotam-se rapidamente.

---

## 2. Comparação de Custos (Março 2026)

### Modelos mais baratos (input / output por 1M tokens)

| Modelo                     | Input      | Output     | Notas                                      |
|----------------------------|-----------|------------|---------------------------------------------|
| **Gemini 2.0 Flash-Lite**  | $0.075    | $0.30      | Mais barato da Google, free tier disponível |
| **Gemini 2.5 Flash**       | $0.30     | $2.50      | Atual no PRAGtico, free tier limitado       |
| **Gemini 2.5 Flash-Lite**  | $0.10     | $0.40      | Bom equilíbrio preço/qualidade              |
| **DeepSeek V3.2**          | $0.14     | $0.28      | Compatível com API OpenAI, muito barato     |
| **GPT-5 Nano**             | $0.05     | $0.40      | Ultra-barato, qualidade limitada            |
| **GPT-5 Mini**             | $0.25     | $2.00      | Bom para chatbots                           |
| **Anthropic Haiku 4.5**       | $1.00     | $5.00      | Qualidade alta, custo moderado              |
| **Mistral 7B (hosted)**    | ~$0.05    | ~$0.05     | Self-host gratuito, hosting ~$0.05/M        |

### Embeddings

| Modelo                          | Custo / 1M tokens | Notas                              |
|---------------------------------|-------------------|------------------------------------|
| **Gemini embedding-001**        | ~$0.10            | Atual no PRAGtico                  |
| **OpenAI text-embedding-3-small** | $0.02           | Muito barato, excelente qualidade  |
| **Voyage AI (voyage-3-lite)**   | $0.02             | Especializado em RAG               |
| **Sentence Transformers local** | $0.00             | Gratuito, corre localmente         |

---

## 3. Recomendações por Cenário

### Cenário A: Orçamento mínimo (< 5€/mês)

**Geração:** DeepSeek V3.2 ($0.14/M input)
- Compatível com SDK OpenAI — basta mudar `base_url`
- Qualidade comparável ao GPT-4 para tarefas de chat
- Cache hits reduzem 90% dos custos

**Embeddings:** Sentence Transformers local (`BAAI/bge-m3`)
- Zero custo, corre no servidor
- Bom para português com modelo multilingual

**Implementação:**
```python
# DeepSeek como drop-in replacement
from openai import OpenAI
client = OpenAI(api_key="deepseek-key", base_url="https://api.deepseek.com")
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": prompt}]
)
```

### Cenário B: Equilíbrio qualidade/custo (5–20€/mês)

**Geração:** Gemini 2.5 Flash-Lite ($0.10/$0.40) ou Gemini 2.0 Flash-Lite ($0.075/$0.30)
- Mantém o SDK `google-genai` que já existe
- Alteração mínima no código (só mudar o nome do modelo)
- Free tier generoso para prototyping

**Embeddings:** OpenAI text-embedding-3-small ($0.02/M)
- Muito barato, excelente qualidade
- Armazena vectores em pgvector (já suportado)

### Cenário C: Multi-provider (melhor relação qualidade/preço)

**Estratégia "router":**
- Consultas simples → DeepSeek V3.2 ou Gemini Flash-Lite (80% do tráfego)
- Consultas complexas/operacionais → Gemini 2.5 Flash ou Anthropic Haiku (20%)
- Embeddings → Sentence Transformers local (zero custo)

**Poupança estimada:** 60–80% face ao uso exclusivo de Gemini 2.5 Flash.

---

## 4. Alterações Necessárias no Código

### Opção mais simples: Mudar modelo Gemini
Apenas alterar a variável de ambiente:
```bash
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.0-flash-lite   # ou gemini-2.5-flash-lite
```
Zero alterações no código. A API é a mesma.

### Opção DeepSeek
O projeto já tem abstração de provider em `integrations/llm_provider.py`, usada por `integrations/rag_engine.py`. Para suportar DeepSeek/OpenAI de forma limpa:

1. Implementar um provider compatível com a API OpenAI
2. Ligar esse provider à seleção por ambiente (`LLM_PROVIDER=...`)
3. Selecionar via variável de ambiente: `LLM_PROVIDER=deepseek`

### Opção embeddings locais
Já existe `LocalEmbeddingProvider` em `integrations/llm_provider.py`.

Na prática, esta opção já está implementada:

1. Instalar `sentence-transformers`
2. Opcionalmente definir `EMBEDDING_LOCAL_MODEL`
3. Arrancar a app e deixar o RAG gerar embeddings localmente

---

## 5. Plano de Migração Recomendado

### Fase imediata (sem custo, sem refactor estrutural)
- [ ] Trocar `LLM_MODEL` para `gemini-2.0-flash-lite` (mais barato, free tier maior)
- [ ] Aumentar `EMBEDDING_REQUESTS_PER_DAY` se a quota atual for insuficiente

### Fase curta (1–2 dias de trabalho)
- [x] Embeddings locais com Sentence Transformers
- [ ] Tornar explícita a preferência por embeddings locais em produção

### Fase média (3–5 dias de trabalho)
- [x] Criar abstração multi-provider
- [ ] Suportar DeepSeek como alternativa para geração
- [ ] Implementar routing inteligente (simples → barato, complexo → premium)

---

## 6. Conclusão

A solução mais imediata e sem custos é **trocar o modelo para `gemini-2.0-flash-lite`**
que custa 4x menos que o atual `gemini-2.5-flash` e tem free tier mais generoso.

Para eliminar o problema de quotas de embeddings, **implementar Sentence Transformers
local** remove completamente essa dependência.

A médio prazo, **DeepSeek V3.2** como provider de geração oferece a melhor relação
qualidade/preço do mercado ($0.14/M input), com API compatível com OpenAI.
