# Genesis FinTech
 

Python-среда для аудита синтетических бухгалтерских эпизодов.

## G0 для A и B

- [Общий контракт, схемы и интерфейсы](docs/CONTRACT_G0.md)
- [Архитектура](docs/ARCHITECTURE.md) · [Поручения ролям](docs/START_AGENT.md) · [План](docs/WORK_PLAN.md)
- [Ручной эпизод и ожидаемый ответ](tests/integration/fixtures/g0_partial_payment)

Проверка ручной фикстуры на Python 3.11 без внешних зависимостей:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/contract_g0 -v
```

В PowerShell задай `$env:PYTHONPATH = 'src'` и запусти `python -m unittest discover -s tests/contract_g0 -v`.
