# Изоляция политики на Linux — отчёт роли B (C18, C19, C20)

Проверка выполнена 2026-11-21 в Docker 29.7.2 (Linux aarch64, Docker Desktop на macOS), Python 3.11.11 в контейнерах.
Использовано дерево «PR 2 роли C (`f61e560`) + ветка `work/env-t03` роли B (`f253881`) + эта папка».
Корпуса A ещё нет, поэтому эпизоды взяты как временная замена из фикстуры G0: 10 train и 10 dev, через один «частичная оплата» и её чистый двойник.

## Как повторить

```bash
bash sandbox/run_isolation_checks.sh
```

Скрипту нужны файлы роли C: `compose.yaml`, `Dockerfile`, `judge_adapter.py` и `runner/`. Если папки `episodes/` нет, скрипт сам создаёт временный корпус из фикстуры (`sandbox/make_fixture_corpus.py`) и потом удаляет его. Результаты пишутся в `artifacts/isolation/`. Контейнеры, тома и локальные образы проекта удаляются при любом исходе. Время прогона около 72 с.

## Результаты

| Проверка | Как | Фактический результат |
|---|---|---|
| Тесты в Linux-контейнере | `tests/contract_g0`, `scoring`, `integration` (`GENESIS_FULL_CHECKS=1`), `contract` | Все OK. Тест обмена по Unix-сокету, который на macOS пропускается, здесь прошёл |
| Полный прогон Compose | `docker compose up`: verify и policy — отдельные контейнеры, общий только том IPC, обмен JSONL по Unix-сокету | Код выхода verify 0. Oracle 20/20 с return 1.000, reconciliation в среднем 0.992, mark_all и random −1.000, train-демо 0.992 |
| **C19** награда в closed | Все строки `trajectories.jsonl` | 530 шагов трёх политик, все награды числовой 0 |
| **C20** сеть | Из контейнера политики: TCP на 1.1.1.1:443, UDP на 8.8.8.8:53, DNS | `Network is unreachable`, имя не разрешается (`network_mode: none`) |
| **C20** файлы эпизода и оценщик | Обход всей ФС контейнера политики; импорт `genesis_audit.env/scoring/data/runner`, `judge_adapter`, `contracts.files` | Не найдено ни одного `truth.json`, `provenance.json`, `manifest.json`, `ledger.csv` и других файлов эпизода. Все импорты дают `ModuleNotFoundError`. В `/app` лежат только `policies/`, `contracts/v1.py`, `contracts/wire.py` и `policy_worker.py` |
| **C20** привилегии | `/proc/self/status`, запись в ФС, Docker socket | uid 65532, `CapEff=0`, `NoNewPrivs=1`. Виден только PID 1, то есть свой процесс. `/`, `/app` и `/ipc` только для чтения. `/var/run/docker.sock` отсутствует |
| **C18** реальный тайм-аут, зависшая политика | Политика читает наблюдение и не отвечает | truncated через **60.03 с**, 0 шагов, findings `[]`, return −1.0. Последнее наблюдение: ошибка `timeout` |
| **C18** реальный тайм-аут, медленная политика | Политика отвечает раз в 1.5 с, время на раздумья входит в лимит | truncated через **60.01 с**, 39 шагов, findings `[]`. Награды во всех шагах 0 |

Полный вывод зонда записан в `probe_report_2026-11-21.json`. В `/sys/class/net` видны туннельные интерфейсы ядра Docker VM (`gre0`, `sit0` и другие), но маршрута нет: любые попытки выйти в сеть получают `ENETUNREACH`.

## Что это доказывает и чего нет

- Граница здесь — отдельный контейнер с другим UID и своим mount namespace, без сети и capabilities. Политика видит только сокет, а это не просто скрытое поле Python-объекта.
- Зависшая политика не задерживает итог: раннер завершает эпизод ровно по дедлайну. Сам контейнер политики продолжает жить, пока его не остановит Compose. В `scripts/verify.sh` роли C это делают `up --abort-on-container-exit` и `down`, а в этом скрипте — `trap cleanup`.
- Прогон шёл на Docker Desktop (Linux VM на arm64), а не на «голом» Linux x86_64. Повторить на x86_64-хосте жюри можно той же командой.
- Корпус временный. После PR роли A прогон нужно повторить на её 20 эпизодах (`episodes/train`, `episodes/dev`).
