# Roadmap

## Implementado no MVP

- entrada por Pages, Issue e `workflow_dispatch`;
- validação de URL e detecção de plataforma na interface;
- captura limitada com `yt-dlp`;
- transcrição local com Whisper tiny;
- score auditável de áudio, texto e cena;
- corte de ~60 segundos e miniatura;
- Releases para mídia e JSON para ranking;
- editor com filtros e exportação até 4K;
- descrição e hashtags locais sem API paga;
- testes unitários do núcleo.

## Próximas entregas

1. Selecionar o melhor intervalo de 60 segundos dentro de cada janela, em vez do centro.
2. Gerar legendas ASS com destaque palavra por palavra.
3. Conectar TTS à trilha de narração com mixagem automática.
4. Criar controlador opt-in para múltiplas janelas de uma live.
5. Adicionar feedback positivo/negativo e recalibração dos pesos.
6. Evitar duplicatas entre janelas por hash perceptual.
7. Migrar para worker/GPU e armazenamento externo quando o volume justificar.

