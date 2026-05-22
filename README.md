# Encontrar Novos Projetos

Scanner diário de Alvarás da Prefeitura de Goiânia — detecta novos projetos de construção via GitHub Actions.

## O que faz

- Varredura automática 1× por dia no sistema de Alvarás de Construção da Prefeitura de Goiânia
- Quando encontra um novo ProjetoId, envia um resumo para o Telegram
- Persiste o último ID válido entre execuções

## Arquivos principais

- `scripts/alvara_scanner.py` — script principal
- `scripts/last_valid_id.json` — estado (último ID válido)
- `scripts/.github/workflows/scan.yml` — GitHub Actions

## Configurar no GitHub

1. Vá em **Settings → Secrets → Actions** e adicione:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TELEGRAM_THREAD_ID` (opicional)

2. Abra a aba **Actions** e habilite o workflow

## Para rodar localmente

```bash
pip install requests
export TELEGRAM_BOT_TOKEN="seu-token"
export TELEGRAM_CHAT_ID="seu-chat-id"
python scripts/alvara_scanner.py
```