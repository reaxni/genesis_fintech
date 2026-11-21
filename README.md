# Genesis FinTech

Проект строит воспроизводимую RL-среду для финансового аудита. Роль C подготовила контракт G0, фиксированный эпизод с разбивкой платежа, scorer, доверенный oracle и интеграционный запуск. Для полной проверки нужны модули данных и среды от ролей A и B.

## Быстрая проверка G0

Из корня репозитория на Linux с Python 3.11:

```bash
bash scripts/check.sh
```

На Windows из PowerShell:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests/contract_g0 -v
python -m unittest discover -s tests/scoring -v
python -m unittest discover -s tests/integration -v
```

Тесты R01–R20 и M01–M12 проверяют правила оценки. Ручной эпизод проверяет сумму 50 000 KZT и ожидаемый return 0.992 после четырёх исследований. Часть интеграционных тестов с реальной средой B включается только в полном прогоне.

## Полный запуск после интеграции A и B

```bash
bash scripts/verify.sh
```

Сборка загружает базовый образ при необходимости. Runtime использует подготовленные образы без сети. Команда запускает тесты, проверяет 10 train и 10 dev эпизодов четырьмя политиками и создаёт `artifacts/trajectories.jsonl`, `train_demo.jsonl` и `metrics.jsonl`. Эталон и итоговые метрики хранятся на стороне оценщика. В закрытых наблюдениях политика получает только публичные данные и reward 0.

Рабочая директория жюри внутри образа `verify` — `/app`. В ней импортируется `judge_adapter.py` с `generate_episode(seed, config, output_dir)`, `make_env()` и `score_episode(episode_dir, findings, inspection_steps)`. Реестр типов ошибок, структура вывода, поддерживаемые ключи и стыки A/B описаны в [контракте G0](docs/CONTRACT_G0.md).

| Файл | Назначение |
|---|---|
| docs/WORK_PLAN.md | Стратегия по критериям, сроки, уникальность и сдача |
| docs/ARCHITECTURE.md | Архитектура и границы доверия |
| docs/CONTRACT_G0.md | Общие форматы и интерфейсы A/B/C |
| docs/ROLE_C_HANDOFF.md | Реальные проверки и незакрытые зависимости |
| docs/ACCEPTANCE_MATRIX.md и acceptance.json | 85 локальных пунктов оценки по опубликованным определениям |
| docs/TASKS.md | Задачи, владельцы, зависимости |
| docs/START_AGENT.md | Готовое поручение на реализацию |
| AGENTS.md | Проектные рабочие инструкции |
| agent-config/config.toml и agents/ | Пять подготовленных ролей |
| docs/sources/ | Полные текстовые извлечения первоисточников |

Для реализации открой сессию в этой папке и передай поручение из START_AGENT.md.
Запись в .codex запрещена средой; конфигурации сохранены в agent-config и не активированы клиентом. AGENTS.md задаёт резервное делегирование через обычных subagents.
Наличие файлов не запускает фоновые задания.

Официальный формат custom roles:
https://learn.chatgpt.com/docs/agent-configuration/subagents
https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/config.schema.json

