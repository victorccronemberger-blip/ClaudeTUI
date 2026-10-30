# ClaudeTUI

Aplicativo Windows para usar o terminal Claude Code com os modelos dos seus provedores, por meio de um proxy local baseado no [Free Claude Code](https://github.com/Alishahryar1/free-claude-code).

Distribuição Windows em fase de teste.

## Instalar e usar

1. Baixe **ClaudeTUI-Setup-0.1.2-x64.exe** em [Releases](https://github.com/victorccronemberger-blip/ClaudeTUI/releases).
2. Execute o instalador e abra **ClaudeTUI** pelo atalho da área de trabalho ou menu Iniciar.
3. Em **Provedores e modelos**, conecte sua conta ou informe sua chave, selecione o modelo e clique em **Apply**.
4. Escolha a pasta do projeto na janela inicial e clique em **Abrir terminal**.

Use **`/model`** no terminal para trocar entre os modelos dos provedores já configurados na mesma sessão. Depois de conectar um provedor pela primeira vez, abra um novo terminal para carregar o catálogo atualizado.

O aplicativo inclui Python, Node.js e Gemini CLI. Na primeira abertura, prepara automaticamente Claude Code e Git, verificando os arquivos oficiais antes de instalá-los. Não requer Python, npm, uv ou ajustes manuais de PATH.

O ícone na bandeja permite abrir o aplicativo, acessar os provedores, reiniciar o serviço ou encerrar o ClaudeTUI. Para atualizar, instale a nova versão sobre a anterior. Para desinstalar, use **Aplicativos instalados** do Windows. A desinstalação preserva suas configurações e contas.

## Contas e provedores

- **OpenAI:** conexão OAuth disponível no painel.
- **Gemini CLI (legado):** o Google desativou esse acesso para contas individuais e assinaturas AI Pro/Ultra em 18 de junho de 2026. Essas assinaturas migraram para o Antigravity; este adaptador ACP não oferece acesso ao Antigravity. Veja o [aviso oficial do Google](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals). O acesso por chave da API Gemini é uma conexão separada.
- **QwenCloud API:** chave pay-as-you-go, endpoint regional e IDs dos modelos configuráveis. Esse acesso é separado dos planos de assinatura QwenCloud.
- **Outros provedores:** continuam disponíveis no painel herdado do FCC.

A integração Gemini CLI usa ACP e mantém as ferramentas do projeto sob controle do cliente. Ela não suporta imagens remotas nem todas as opções de amostragem da API. O terminal continua sendo o Claude Code original; este repositório fornece o proxy, as integrações e a distribuição Windows.

## Compilar a distribuição Windows

Para desenvolvimento, use Windows x64, Python 3.14.10 e uv:

```powershell
uv sync --locked --group windows-build
uv run --no-sync python scripts/build_windows.py
```

O resultado fica em `dist/ClaudeTUI-Setup-0.1.2-x64.exe`. O build baixa as versões fixadas em `desktop/windows/runtime-lock.json`, verifica seus hashes, usa o lockfile npm e gera o aplicativo e o instalador Inno Setup. O teste `ClaudeTUI-Terminal.exe --self-check` verifica a distribuição; `--smoke-test` também executa as ferramentas incluídas e o ciclo de vida da API em processo, sem chamar provedores.

O serviço usa apenas o endereço local. Configurações continuam em `~/.fcc`; ferramentas gerenciadas e preferências do aplicativo ficam em `%LOCALAPPDATA%/ClaudeTUI`. Atualizações do instalador substituem o aplicativo e preservam esses dados.

Os instaladores e scripts upstream documentados em [README.upstream.md](README.upstream.md) instalam o FCC original. A publicação automática herdada no PyPI permanece desativada, a menos que o mantenedor configure explicitamente `FCC_ENABLE_RELEASES=true`.

## Licença

[AGPL-3.0-only](LICENSE). Créditos e documentação original preservados em [README.upstream.md](README.upstream.md). Os componentes de terceiros mantêm suas respectivas licenças. Projeto independente, sem afiliação com Anthropic, OpenAI ou Google.
