# Arquitetura

## Componentes

| Componente | Responsabilidade |
|---|---|
| `frontend/` | SPA estática, ranking, solicitação de captura e editor |
| `scripts/cutai/` | validação, mídia, transcrição, score, metadados e ranking |
| `.github/workflows/` | captura, edição e publicação do Pages |
| `data/ranking.json` | índice pequeno e versionado dos cortes |
| GitHub Releases | MP4, miniaturas e exportações |

## Score

O score de 0 a 100 usa a fórmula:

`total = áudio × 0,35 + transcrição × 0,45 + cena × 0,20`

O componente de fala considera densidade de termos emocionais, ênfase e quantidade útil de palavras. Áudio considera volume médio e diferença até o pico. Cena considera cortes visuais acima do limiar de 0,35. Os três componentes são guardados no ranking para auditoria.

## Segurança

- URLs são extraídas do payload JSON da Issue por Python, sem avaliar texto como shell.
- Apenas `http` e `https` sem credenciais embutidas são aceitos.
- IDs, filtros e resoluções de edição passam por listas permitidas.
- O token do GitHub é temporário e não é salvo.
- A captura limita duração e resolução para reduzir abuso e custos acidentais.

## Próximo desenho para lives longas

Um workflow controlador pode agendar janelas sequenciais via API, respeitando o limite de jobs e uma quantidade máxima definida pelo dono. Para evitar duplicatas, cada janela deverá registrar o timestamp da mídia e um hash perceptual. Isso não está habilitado no MVP para não consumir a cota sem controle explícito.

