# Состояние Genesis

| Этап | Состояние |
|---|---|
| T00 · G0 | Контракт v1.0 в `main`. [PR #1](https://github.com/reaxni/genesis_fintech/pull/1) |
| T03 · среда и политики | В `main`. [PR #3](https://github.com/reaxni/genesis_fintech/pull/3) |
| T02/T05/T06/T07 · данные и проверка | Генератор, независимый верификатор и 20 открытых эпизодов в `main`. [PR #4](https://github.com/reaxni/genesis_fintech/pull/4) |
| T04 · оценщик и запуск | Оценщик, адаптер жюри и команды проверки в `main`, коммит `d2aaec3`. [PR #2](https://github.com/reaxni/genesis_fintech/pull/2) |
| T08 · сборка для сдачи | VERIFIED на Ubuntu командой `bash scripts/verify.sh`. Сборка и отчёт в [PR #5](https://github.com/reaxni/genesis_fintech/pull/5). |
| T09/T11 · ревью и финальная сдача | TODO. Проверка собранного комплекта и результатов. |

Полный запуск из корня — `bash scripts/verify.sh`. Измерения и ограничения собраны в [отчёте о проверке](ROLE_C_HANDOFF.md). Скрытые проверки проводит жюри.
