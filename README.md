# CutCutAi

Ferramenta open source para capturar transmissões em segmentos, transcrever o áudio, estimar o potencial de cada momento e produzir cortes classificados por score. A interface é publicada no GitHub Pages; processamento e exportações acontecem no GitHub Actions; vídeos ficam em GitHub Releases.

> **MVP:** esta versão prova o fluxo completo com uma captura de 1–15 minutos por execução. Acompanhamento recorrente de uma live longa exige novas execuções e consome a cota do Actions.

## Como funciona

1. Na interface, cole o link da live e abra a solicitação preparada.
2. O pedido inicia `process-live.yml`.
3. `yt-dlp` captura um segmento; `faster-whisper` transcreve em CPU.
4. A análise procura histórias completas e pontua os candidatos.
5. FFmpeg produz cortes limpos com no mínimo 60 segundos; a quantidade não é forçada quando não há candidatos bons suficientes.
6. Cada corte é publicado com MP4, miniatura e uma trilha `.captions.json` editável.
7. `data/ranking.json` é atualizado e o GitHub Pages publica o ranking.
8. No Editor, o usuário escolhe legenda, cores, destaque, posição, tamanho, filtro e resolução.
9. A exportação renderiza um novo MP4 em um Release independente. O corte original permanece inalterado.

## Ativação inicial

No repositório, abra **Settings → Pages → Build and deployment** e escolha **GitHub Actions**. Depois execute manualmente o workflow **Publicar interface** uma vez. Para repositórios privados, confirme que seu plano permite Pages.

Os workflows precisam destas permissões em **Settings → Actions → General → Workflow permissions**:

- `Read and write permissions`
- permissão para o Actions criar commits e Releases

Não é necessário cadastrar chave de API. O `GITHUB_TOKEN` temporário é fornecido pelo próprio Actions.

### Proxy residencial para YouTube e TikTok

Runners públicos do GitHub usam IPs de datacenter e podem receber bloqueios antirrobô. Para captura mais estável, cadastre uma URL de proxy residencial em **Settings → Secrets and variables → Actions → New repository secret**:

- nome: `CUTAI_PROXY_URL`
- valor: URL completa entregue pelo provedor, no formato `http://usuario:senha@host:porta`

O segredo é injetado somente durante a captura. Use sessão fixa (*sticky session*) durante cada captura para evitar troca de IP no meio da live. O custo é por tráfego; limite resolução e duração para controlar gastos.

Para YouTube, o projeto também aceita a sessão protegida configurada no secret `YOUTUBE_COOKIES`.

## Uso

### Pela interface

Acesse o endereço publicado no GitHub Pages, cole um link e finalize a solicitação no GitHub. O resultado entra no ranking ao terminar.

### Manualmente pelo Actions

Abra **Actions → Processar live → Run workflow**, informe a URL e o tamanho da captura entre 60 e 900 segundos.

### Edição

Escolha um corte no Editor. A interface carrega a trilha real de legendas sincronizada com o vídeo e permite configurar estilo (`viral`, `clean` ou sem legenda), cor, destaque automático, posição, tamanho, filtro e resolução. A solicitação inicia `edit-clip.yml`, que baixa o corte limpo e a trilha de legendas, renderiza a edição e publica o vídeo final em um **novo Release independente**.

O Release original do corte não é alterado pela exportação.

## Limitações reais

- **Ainda existe uma etapa visível no GitHub.** O Pages é uma interface estática; nesta arquitetura MVP, pedidos de processamento e exportação ainda são enviados por Issues. Um backend autenticado é o caminho para remover essa etapa para usuários finais.
- **Não é tempo real literal.** A alternativa dentro do GitHub é capturar e processar janelas. Cada nova janela exige uma execução.
- **GitHub Actions não é infraestrutura gratuita ilimitada.** Repositórios privados usam a cota de minutos do plano. Jobs hospedados têm duração máxima e podem aguardar em fila.
- **IA em CPU é mais lenta que workers com GPU.** O pipeline atual prioriza funcionamento dentro do GitHub Actions.
- **4K pode ser upscale.** Quando a fonte é 1080p, selecionar 2160p aumenta a resolução de saída, mas não cria detalhe nativo que não existe na fonte.
- **Fontes podem bloquear downloads.** Lives privadas, DRM, login, geobloqueio e mudanças nas plataformas podem impedir o `yt-dlp`.
- **Proxy pode ser necessário.** YouTube e TikTok bloqueiam com frequência IPs de datacenter. Proxy residencial gera cobrança por GB e não elimina DRM ou exigências de conta.
- **Armazenamento não é infinito.** MP4, miniaturas e metadados publicados em Releases continuam sujeitos às políticas e cotas do GitHub.
- **Score não garante viralização.** Ele ordena sinais observáveis e deve continuar sendo calibrado com feedback e dados reais.
- **Direitos autorais e privacidade.** Processe apenas transmissões que você tem autorização para baixar e reutilizar.

Consulte `docs/arquitetura.md` e `docs/roadmap.md` para detalhes internos.

## Desenvolvimento local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest
```

Para executar o pipeline completo, instale FFmpeg, `yt-dlp` e o extra `ai`:

```bash
pip install -e '.[ai]'
pip install 'yt-dlp[default,curl-cffi]'
python -m cutai.pipeline --url 'LINK_DA_LIVE' --capture-seconds 300
```

## Evolução para produção

O fluxo atual foi desenhado para validar o produto usando GitHub Pages + Actions + Releases. Para transformar a interface em uma experiência transparente para clientes, a próxima camada deve ser um backend autenticado que receba os pedidos do site, acompanhe o estado dos jobs e devolva os resultados sem expor Issues ou Actions. Para maior escala, o processamento longo pode migrar para workers com GPU e object storage mantendo a mesma separação entre **corte original limpo** e **exportações editadas**.
