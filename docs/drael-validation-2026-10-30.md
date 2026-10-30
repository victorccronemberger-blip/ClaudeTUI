# Drael — validação da versão 0.2.1

O provedor `drael` usa o endpoint oficial `https://drael.sh/v1`, com chave da
API, catálogo autenticado e o transporte OpenAI Chat já compartilhado pelo
proxy. A chave do usuário fica na configuração local; não integra o código,
o instalador ou este relatório.

O catálogo real retornou `drael-v1`, contexto de 122.880 tokens, entrada de
texto/imagem, suporte a ferramentas e raciocínio low/medium/high. O identificador
do modelo, o contexto e as modalidades são descobertos pela API.

## Testes reais

- API de produção: o modelo apareceu como `drael/drael-v1` e respondeu `OK`.
- A solicitação de ferramenta `Read` foi devolvida ao chamador; a resposta
  seguinte incorporou corretamente o resultado sintético recebido.
- Entrada de imagem pela rota Messages em streaming: um PNG vermelho foi
  identificado como `red`.
- Pacote congelado: inicialização da API, recursos e runtimes verificados.
- Atualização local para 0.2.1: hashes de configuração e contas preservados.
- Aplicativo instalado: Drael → Gemini → OpenAI responderam `OK` mantendo o
  mesmo identificador do serviço. O modelo padrão existente foi preservado.
- Claude Code real, conectado ao aplicativo instalado e usando Drael: leu um
  arquivo temporário e escreveu uma cópia correta; a comparação do conteúdo
  passou e o processo terminou com saída zero e `CLI_DRAEL_OK`.

Os testes não enviaram arquivos do usuário para o provedor. Os arquivos e a
imagem usados como entrada foram criados para essas verificações.

## Contrato e limites

As ferramentas do chamador são enviadas explicitamente. Sem ferramentas,
o proxy envia um catálogo vazio e `tool_choice: none`, pois omitir o campo
habilita o catálogo e o loop próprios do Drael.

O raciocínio é transmitido em seu canal próprio e preservado na continuação
com ferramentas. A escolha de desligá-lo mapeia para low: a API Drael não
oferece off por chave e também não aplica `max_tokens`. A interface informa
essas limitações. Plano, cota e cobrança são controlados pelo provedor.

Validação automatizada: 1.605 casos aprovados e 8 ignorados, cobrindo o novo
perfil, configuração, contratos, descoberta, transportes, runtime, painel,
OpenAI e Google. Ruff e ty passaram.

Referências: [API](https://drael.sh/docs),
[chat e raciocínio](https://drael.sh/docs/chat),
[ferramentas](https://drael.sh/docs/tools).
