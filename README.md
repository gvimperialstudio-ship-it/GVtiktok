# ERP multi-CNPJ para autopeças

Sistema de estoque e operação para um vendedor de autopeças que opera Mercado Livre, Shopee, loja própria e CNPJs parceiros. A aplicação roda em Python 3.12. O desenho prevê 100% Oracle Cloud (OCI); este repositório ainda não cria a infraestrutura.

Estoque, status de NF-e e qualquer decisão fiscal são código determinístico. Nenhum modelo de linguagem escolhe quantidade, pausa de anúncio ou imposto. Um supervisor externo só lê os endpoints `/ops/*` (com um Bearer próprio) e dispara retentativas idempotentes.

## O que este PR entrega

- API FastAPI e worker assíncrono de sincronização de estoque.
- PostgreSQL como única fonte da verdade do estoque. Redis é só fila e limite de taxa por CNPJ.
- Schema inicial e migration Alembic.
- Baixa de estoque atômica (`UPDATE ... WHERE qty_available >= :qty RETURNING`), com explosão de kit na mesma transação e idempotência em `processed_events`.
- `SHADOW_MODE=true` por padrão: o worker registra o PUT que enviaria e não chama o canal.
- Endpoints `/ops/health`, `/ops/stock-audit` e `/ops/dlq` (listar e retentar).
- Provedor de segredos plugável: `local` (env/arquivo, só desenvolvimento) e stub do OCI Vault que falha fechado.
- Notificador de webhook assinado com HMAC-SHA256. Sem URL, ele só assina e registra log.
- Testes pytest contra PostgreSQL real.

## O que ficou de fora de propósito

- Clientes HTTP de Mercado Livre, Shopee e loja própria. As interfaces existem; as implementações recusam a chamada.
- Regras fiscais. Não há CFOP, CST, CSOSN, ICMS, PIS, COFINS nem DIFAL. `invoices` é um placeholder estrutural com máquina de estados. Isso espera aprovação do contador.
- Emissão real de NF-e (XML, SEFAZ, certificado A1 em uso).
- Infraestrutura OCI: Vault de verdade, rede, Object Storage, filas gerenciadas, deploy.
- Entrada de pedidos pelo webhook do marketplace. O serviço de baixa existe e é o que o retry da DLQ chama; o adaptador de cada canal entra num PR seguinte.
- Kits aninhados. Só um nível: o componente não pode ser outro kit.

## Estrutura

```
src/erp/
  config.py            configuração; SHADOW_MODE padrão true
  secrets/             interface, provedor local e stub OCI Vault
  db/models.py         schema SQLAlchemy 2
  stock/               baixa atômica, quantidade vendável, pausa vs atualização
  channels/            interface e stubs (Mercado Livre, Shopee, loja própria)
  queue/               fila Redis e rate limit por CNPJ
  workers/             consumidor de sincronização de estoque
  invoices/            só transições de status
  ops/                 /ops, auditoria, webhook
alembic/               migration inicial
config/config.sample.yaml
docker-compose.yml     postgres, redis, api, worker
tests/
```

## Estoque

Há um galpão compartilhado, `galpao_central`, e um depósito Full por empresa, `full_ml_<cnpj>`. O saldo Full não entra na soma do saldo compartilhado. A view `shared_stock_balances` e a consulta de quantidade vendável filtram `warehouse_type = 'shared'`.

A baixa usa uma única sentença condicional dentro da transação. Se o saldo não cobre a quantidade, o `UPDATE` não altera a linha, a transação desfaz o evento e o pedido pode ser tentado de novo. Um kit vira os componentes nessa mesma transação, com as linhas bloqueadas em ordem estável de SKU. O mesmo `(channel, external_event_id)` não baixa duas vezes.

Quando o saldo compartilhado muda, o sistema enfileira um ponteiro por anúncio ativo que não é Full. A mensagem no Redis leva só ids. O worker relê a quantidade no PostgreSQL.

Quantidade publicada = disponível compartilhado − `safety_threshold`. Se o resultado não for positivo, a decisão é pausar (`quantity` 0) em vez de atualizar.

## Modo sombra

`SHADOW_MODE` vale `true` se a variável não for definida. Nesse modo o worker grava um log `SHADOW_MODE would PUT ...` com o JSON do PUT e marca o anúncio como `shadow_logged`. Ele não altera `last_synced_qty` e não abre HTTP. O audit de estoque continua mostrando a diferença entre o saldo e a última quantidade realmente enviada.

## Segredos

Nenhum segredo fica no código, na migration ou no exemplo de config. O banco guarda referência (OCID ou nome), nunca o valor.

- `local`: resolve `env:NOME` e `file:/caminho`. Recusa OCID.
- `oci_vault`: stub. `get_secret` levanta erro. Não devolve valor inventado.

O Bearer de `/ops` é outro segredo, separado de OAuth dos canais. Toda escrita em `/ops` (hoje, o retry da DLQ) gera linha em `ops_audit_log`. O token em si não é gravado.

## Como rodar localmente

Requisitos: Docker com Compose, ou Python 3.12 com PostgreSQL 16 e Redis 7 na máquina.

```bash
cp .env.example .env
# troque cada change-me. A senha local precisa ser segura em URL.

docker compose up --build
```

A API aplica `alembic upgrade head` na subida. Health:

```bash
curl -s -H "Authorization: Bearer change-me" http://localhost:8000/ops/health
```

Sem Docker, com Postgres e Redis locais:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export $(grep -v '^#' .env | xargs)
alembic upgrade head
uvicorn erp.main:create_app --factory --host 0.0.0.0 --port 8000
python -m erp.workers.runner
```

`CONFIG_FILE` aponta para um YAML no formato de `config/config.sample.yaml`. Variável de ambiente ganha do arquivo. O YAML de exemplo só tem placeholder.

Arquivos ignorados pelo git: `.env`, `*.pfx`, `tokens.json`.

## Testes

Os testes usam PostgreSQL de verdade. Eles apagam o schema `public` do banco indicado. O nome do banco precisa conter `test`.

Com Docker (testcontainers sobe Postgres 16):

```bash
pip install -e ".[dev]"
pytest
```

Sem Docker, apontando para um Postgres local:

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://erp:change-me@localhost:5432/erp_test
export TEST_REDIS_URL=redis://localhost:6379/15
pytest
```

O Redis dos testes é fila. O estoque assertado sai do Postgres. A suíte cobre:

- duas baixas concorrentes da última unidade: uma vence, saldo não fica negativo;
- evento duplicado não baixa de novo;
- explosão de kit e rollback se um componente não cobre;
- fan-out em modo sombra, sem HTTP, sem somar Full, pausa quando o limiar zera a quantidade;
- Bearer de `/ops` lido do provedor, retry idempotente e trilha de auditoria.

## Endpoints

| Método | Caminho | Efeito |
| --- | --- | --- |
| GET | `/ops/health` | Postgres, Redis e o valor de `shadow_mode` |
| GET | `/ops/stock-audit` | Compara saldo (compartilhado ou Full, sem somar) com `last_synced_qty` |
| GET | `/ops/dlq` | Lista a fila morta |
| POST | `/ops/dlq/{id}/retry` | Retentativa idempotente. Grava `ops_audit_log` |

Todos exigem `Authorization: Bearer <token>`.

## Decisões

- `orders` é único em `(channel, company_id, external_order_id)`. `company_id` é a loja; `companies.cnpj` é único.
- Sincronização automática não inclui anúncio Full e não dispara quando a baixa foi no depósito Full. O saldo Full não muda o que o galpão compartilhado publicaria.
- Em modo sombra, `last_synced_qty` fica como estava. O audit mostra o que ainda não foi enviado de verdade.
- O retry da DLQ que encontra o evento já processado responde `already_resolved` ou `duplicate` e não baixa de novo.
- OCI Vault, canais e fiscal ficam explícitos como TODO em vez de uma implementação falsa que pareça pronta.
