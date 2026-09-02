# CutCutAi

Ferramenta open source para capturar transmissões em segmentos, transcrever o áudio, estimar o potencial de cada momento e produzir cortes curtos classificados por score. A interface é publicada no GitHub Pages; processamento e exportações acontecem no GitHub Actions; vídeos ficam em GitHub Releases.

> **MVP:** esta versão prova o fluxo completo com uma captura de 1–15 minutos por execução. Acompanhamento recorrente de uma live longa exige novas execuções e consome a cota do Actions.

## Como funciona

1. Na interface, cole o link da live e abra a Issue preparada.
2. A Issue recebe o label `live-request` e inicia `process-live.yml`.
3. `yt-dlp` captura um segmento; `faster-whisper` transcreve em CPU.
4. O score combina fala (45%), energia do áudio (35%) e mudanças de cena (20%).
5. FFmpeg produz um corte de aproximadamente 60 segundos e uma miniatura.
6. Os arquivos são anexados a um Release e `data/ranking.json` é atualizado.
7. O GitHub Pages publica o novo ranking automaticamente.

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

O segredo é injetado somente durante a captura, não aparece no repositório e é removido de mensagens de erro. Use sessão fixa (*sticky session*) durante cada captura para evitar troca de IP no meio da live. O custo é por tráfego; limite resolução e duração para controlar gastos.

## Uso

### Pela interface

Acesse o endereço publicado no GitHub Pages, cole um link e finalize a criação da Issue. O progresso aparece na aba **Actions** e o resultado entra no ranking ao terminar.

### Manualmente pelo Actions

Abra **Actions → Processar live → Run workflow**, informe a URL e o tamanho da captura entre 60 e 900 segundos.

### Edição

Escolha um corte no Editor. A interface prepara uma Issue `edit-request`; o workflow baixa o asset do Release, aplica o filtro e a resolução escolhidos e envia a nova versão ao mesmo Release. A base atual já renderiza filtros e até 4K. Legendas animadas e narração TTS estão representadas na interface, mas serão conectadas ao renderizador na próxima etapa do produto.

## Limitações reais

- **Não é tempo real literal.** A alternativa dentro do GitHub é capturar e processar janelas. Cada nova janela exige uma execução.
- **GitHub Actions não é infraestrutura gratuita ilimitada.** Repositórios privados usam a cota de minutos do plano. Jobs hospedados têm duração máxima e podem aguardar em fila.
- **IA em CPU é mais lenta e menos precisa.** O padrão é Whisper `tiny` com quantização `int8`. Modelos maiores melhoram a transcrição, mas aumentam bastante o tempo.
- **4K é pesado.** O runner padrão não tem GPU; o encoder x264 usa CPU. A interface avisa antes da solicitação.
- **Fontes podem bloquear downloads.** Lives privadas, DRM, login, geobloqueio e mudanças nas plataformas podem impedir o `yt-dlp`.
- **Proxy pode ser necessário.** YouTube e TikTok bloqueiam com frequência IPs de datacenter. Proxy residencial gera cobrança por GB e também não elimina DRM ou exigências de conta.
- **Armazenamento não é infinito.** MP4 e miniaturas ficam em Releases, nunca em commits. Ainda se aplicam políticas e cotas do GitHub.
- **Score não garante viralização.** Ele ordena sinais observáveis e precisa ser calibrado com feedback e dados reais.
- **Direitos autorais e privacidade.** Processe apenas transmissões que você tem autorização para baixar e reutilizar.

Consulte [docs/arquitetura.md](docs/arquitetura.md) para detalhes e [docs/roadmap.md](docs/roadmap.md) para a evolução planejada.

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
pip install yt-dlp
python -m cutai.pipeline --url 'LINK_DA_LIVE' --capture-seconds 180
```

## Evolução para produção

Para escala, mova o processamento longo para workers com GPU e object storage. APIs como Whisper e um LLM podem elevar a qualidade da transcrição, dos títulos e da análise contextual. O Pages pode continuar como vitrine, mas um backend autenticado deve substituir Issues quando houver vários usuários.
