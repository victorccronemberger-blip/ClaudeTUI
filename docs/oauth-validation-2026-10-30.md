# Validação de contas — 30/10/2026

Os estados abaixo distinguem login, catálogo e geração real. Um catálogo de
modelos, sozinho, não comprova que uma conta tem acesso à inferência.

| Conexão | Login | Catálogo | Geração real | Situação da distribuição |
| --- | --- | --- | --- | --- |
| OpenAI / ChatGPT | Concluído | 8 modelos | `OK` pelo `/v1/messages` do aplicativo instalado | Validado também no instalador 0.2.0 |
| Gemini CLI legado / Google AI Pro | Acesso retirado pelo Google | Não aplicável a esse plano | Não disponível por esse cliente | O painel informa a migração para Antigravity |
| Google / Antigravity CLI 1.4.12 | Sessão oficial existente reutilizada | 13 modelos | Gemini Flash e Argon responderam `OK`; ferramentas e continuação verificadas | Instalador 0.2.0 instalado e validado |
| Anthropic / Claude Code | Sem conta disponível para o teste | Inicialização do SDK retorna catálogo, sem comprovar acesso | Não testada com conta real | OAuth Anthropic ainda não publicado |

## Evidência Google

O primeiro teste falhou na elegibilidade do cliente oficial. Depois que o
usuário instalou e testou seu cliente, o executável em `%LOCALAPPDATA%/agy/bin`
respondeu normalmente. O bloqueio observado anteriormente não persistiu;
nenhuma restrição de conta foi contornada pelo proxy.

O adaptador usa o cliente oficial e o keyring dele, sem copiar tokens para o
FCC. A conexão só é salva depois de descobrir modelos e receber uma resposta
real. As chamadas usam diretórios temporários e configurações por sessão;
mantêm créditos adicionais desativados e encaminham ferramentas por MCP ao
chamador. A entrada é somente texto, conforme o protocolo do CLI.

Validações reais, além dos testes automatizados:

- API de produção: Google Flash → OpenAI → Google Argon no mesmo runtime,
  sem reinício, com respostas `OK`.
- Ferramenta `Read` devolvida ao chamador e continuação com seu resultado.
- Aplicativo instalado 0.2.0: 13 modelos Google e 8 OpenAI; os três modelos
  acima responderam mantendo o mesmo identificador de serviço.
- Claude Code 2.1.314, usando o proxy instalado e Gemini Flash: leu um arquivo
  temporário, escreveu seu conteúdo em outro arquivo e respondeu
  `CLI_GOOGLE_OK`. O conteúdo foi comparado; saída zero e nenhuma negação de
  permissão. Essa geração usou Google, sem uma conta Anthropic.
- Atualização do instalador preservou os hashes da configuração e dos arquivos
  de contas. Gemini Flash ficou selecionado como modelo padrão.
- Smoke test do pacote congelado confirmou a API, os recursos e as versões
  dos clientes nativos incluídos/gerenciados.

URLs de verificação, códigos, credenciais e identidade da conta não integram
este relatório.

## Experimento Anthropic sem conta, fora da distribuição

Uma prova local com Claude Agent SDK 0.2.175 e o Claude Code gerenciado 2.1.314
validou a fronteira de ferramentas usando uma API fictícia em loopback:

- A única ferramenta MCP anunciada foi `mcp__fcc__Read`, além do marcador
  interno `DeferredToolPlaceholder` do cliente.
- O cliente entregou os argumentos `{"path":"README.md"}` ao callback.
- O callback não leu arquivos; devolveu o controle ao chamador.
- A interrupção encerrou a tarefa e o cliente sem o aviso de pipe não fechado
  observado no primeiro experimento com cancelamento abrupto.

Essa prova não valida OAuth, assinatura, faturamento ou inferência Anthropic.

## Versões

O import original `8e92b86` tem `src`, `pyproject.toml` e `uv.lock` idênticos
à tag upstream `v6.18.5`, commit
`8dfff83287d3133642ef8ef44e55518c6f41c287`.

`0.2.0` é a versão independente do aplicativo/instalador ClaudeTUI. O painel,
rodapé Windows e diagnóstico instalado exibem também a base FCC 6.18.5.

Validação: 1.537 testes aprovados e 8 ignorados na seleção de provedores,
configuração, contratos, runtime, administração e distribuição Windows.
Os cinco casos de identidade Python/uv precisaram do Git Bash gerenciado no
PATH do processo de testes. Ruff e ty passaram. A suíte não substitui os
testes reais de cada conta.

## Referências oficiais

- [Autenticação Antigravity](https://antigravity.google/docs/install/)
- [Modo headless Antigravity](https://antigravity.google/docs/cli/headless/)
- [Fim do Gemini CLI para contas consumidoras](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)
- [Claude Agent SDK Python](https://code.claude.com/docs/en/agent-sdk/python)
