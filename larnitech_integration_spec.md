## Цель

Кастомная интеграция Home Assistant для контроллеров Larnitech через API2.
HA получает список устройств, их состояние (push + страховочный poll) и
управляет ими. Первая версия — только чтение для двух типов виджетов.

## Подключение

| Параметр | Значение |
|---|---|
| Протокол | WebSocket, API2 |
| Режим | выбирается в config_flow: **local** или **cloud** |
| Local | `ws://<host>:<port>/api`, порт по умолчанию 2041 |
| Cloud | `wss://<serial>.in.larnitech.com:8443/api` |
| Авторизация | `{"request":"authorize","key":"<key>"}` после connect |

**Мультисервер.** Один HA — несколько config entry, по одному на сервер
Larnitech. Каждый entry = отдельное WS-соединение, свой набор сущностей.

### Настройки и где они меняются

**Подключение** (`entry.data`) — меняется через **Reconfigure** (запись → ⋮ → Reconfigure):

| Поле | Режим |
|---|---|
| `connection_type` | оба (`local`/`cloud`) |
| `host` / `port` | local |
| `serial` | cloud |
| `key` | оба |

**Поведение** (`entry.options`, все по умолчанию **on**) — меняется через
**Options** (карточка интеграции → Configure); при сохранении запись
перезагружается:

| Галочка | Что делает |
|---|---|
| `auto_remove` | удалять устройство, если пропало (после 2 пустых снапшотов) **или сменило тип** (сразу) |
| `update_names` | подтягивать имена из Larnitech (ручные переименования не трогает) |
| `use_areas` | создавать комнаты как в Larnitech и раскладывать устройства |
| `update_areas` | переносить устройство при смене комнаты (требует `use_areas`; перезаписывает ручную привязку) |

Чтение: `toggle(entry, key)` = `options` → `data` → default `True`.

### Жизненный цикл (reconcile)

Только по **полному снапшоту** `get-devices` (не по событиям):
- **add** — платформы добавляют новые addr своего типа через `async_add_listener`;
- **remove** — координатор сносит device из registry (каскадом сущности) после
  2 пустых снапшотов или сразу при смене типа (`device.model != new type`);
- **name/area sync** — обновляет имя/комнату в device registry по галочкам.

Кнопка `button.<serial>_resync_names` (entity_category=config) — принудительно
сбрасывает все имена к Larnitech, **включая** ручные переименования.

## Поток данных

```
Larnitech SVIT ──ws──► LarnitechClient (1 на entry)
   status-subscribe (push, основной канал)
   get-devices каждые 60 с (страховочный snapshot)
                    │
                    ▼
         async_dispatcher_send → сущности → async_write_ha_state
```

- **Push** через `status-subscribe` — постоянное соединение, слушатель ловит
  `{"event":"statuses",...}` и дёргает **refresh** (а не декодирует hex).
  Любое событие → `get-devices` (декодированный путь), дебаунс 2 с.
- **Poll** через `get-devices` раз в 60 с — страховка от потерянных событий.
- **Reconnect.** Супервайзер переподключается с backoff, после реконнекта
  сразу тянет свежий снимок.

### Находка: формат `status` — hex или decoded (стенд, 2026-06-24)

У сессии есть «уровень детализации». По умолчанию `status` приходит как
**hex-строка** (`"0x6018"`). Любой запрос с `status:"detailed"` переключает
сессию в **decoded** (вложенный объект, `{"state":"on"}`) — **липко** до конца
соединения. Касается и подписки, и её событий.

| Что отправлено в сессии | Формат `status` |
|---|---|
| `status-subscribe` (без `status`) | hex-строка `"0x01"` |
| `status-subscribe` + `status:"detailed"` | decoded `{"state":"on"}` |
| `get-devices status:"detailed"` → затем subscribe | decoded |
| `get-devices` (без `detailed`) → subscribe | hex-строка |

**Решение (реализовано):** подписываемся сразу с `status:"detailed"` → события
приходят декодированными. Их **применяем напрямую** (merge в нужный addr,
без перечитывания всех устройств). Если прилетит hex-строка (краткое окно до
detailed) или неизвестный addr — фолбэк на полный `get-devices`. Событие
несёт только изменившиеся ключи статуса → именно merge, не replace.

### Находка: облако не отвечает на WS-ping

Клиентский `ping_interval` роняет соединение по `keepalive ping timeout` —
облако Larnitech не шлёт pong. Пинги отключены (`ping_interval=None`);
живость соединения и keepalive обеспечивает poll раз в 120 с.

### Находка: битый JSON у type=json

Виджеты `type:"json"` шлют `"status":{{...}` с удвоенной `{` — ломает
строгий парсинг всего ответа. Клиент чинит таргетной заменой
`"status":{{` → `"status":{` перед `json.loads`.

## Идентификация сущностей

- `unique_id` = `<serverSerial>_<ID>_<SUBID>` — `addr` формата `294:240`
  разбирается на ID и SUBID, префикс serial разводит одинаковые адреса на
  разных контроллерах. Пример: `411317ac_294_240`.
- `friendly_name` = `name` из get-devices как есть (язык клиента).
- Привязка к HA area — только если `create_areas = true`: area из Larnitech
  создаётся в HA и устройство привязывается к ней.

## Маппинг виджетов

### Версии 1–2 — чтение + запись

Поля подтверждены живым `get-devices`/`status-set` со стенда `411317ac`.

| Larnitech type | HA platform | Чтение | Запись |
|---|---|---|---|
| `temperature-sensor` | `sensor` | `status.state` (число); `device_class=temperature`, `°C` | — |
| `lamp` | `light` | `status.state` `"on"`/`"off"` → is_on (без яркости) | `status-set {"state":"on"/"off"}` |

Замечания по реальным данным:
- У `temperature-sensor` температура лежит в `status.state` (не отдельный
  ключ `temperature`). Значение — float, единица `°C`.
- У `lamp` помимо `state` есть `auto-state` (bool) — в v1 не используется.
- `addr` встречается с большими ID (`331:1`, `2048:247`) — разбор по `:`
  обязателен, не предполагать однозначные ID.

### Дальнейшие фазы

| Фаза | Содержание | Статус |
|---|---|---|
| 1 | Чтение lamp + temperature-sensor, polling | ✅ |
| 2 | Push (subscribe→refresh) + запись lamp (`status-set`) | ✅ |
| 3 | dimmer-lamp (brightness), AC (`climate`), cover, остальные виджеты | — |

**Команда записи lamp (проверено):** `{"request":"status-set","addr":"1:4",
"status":{"state":"on"}}` → `{"response":"status-set","devices":[{"addr":
"1:4","success":true}]}`. Событие изменения: `{"event":"statuses","devices":
[{"addr":"1:4","status":"0x01"}]}` (on=`0x01`, off=`0x00`).

## Структура компонента

```
custom_components/larnitech/
├── manifest.json      iot_class: local_push, requirements: websockets
├── const.py           DOMAIN, дефолтный порт, имена сигналов
├── config_flow.py     форма подключения + опция create_areas
├── __init__.py        async_setup_entry: client, forward платформам
├── client.py          WS connect/authorize/get-devices/subscribe, reconnect, 60s poll
├── sensor.py          temperature-sensor → sensor
└── light.py           lamp → light (read-only)
```

## Вне объёма v1–v2

- Любые типы виджетов кроме `temperature-sensor` и `lamp` — фаза 3.
- Яркость lamp / dimmer-lamp — фаза 3.
- HACS-публикация и логотип (brands repo) — после фазы 3.

## Стенд для разработки

| Параметр | Значение |
|---|---|
| Режим | cloud |
| Serial | `411317ac` |
| URL | `wss://411317ac.in.larnitech.com:8443/api` |
| Устройств в ответе | 59 |
| Целевых для v1 | lamp ×5 (`1:1`–`1:4`, `1:9`), temperature-sensor ×12 |

## Производительность (стенд, cloud)

| Операция | Время | Трафик |
|---|---|---|
| `get-devices status=detailed` | 170–210 мс | 11.4 КБ / 59 устройств |
| connect + authorize | ~800 мс | разово на соединение |
| событие (decoded, прямое применение) | ~150 мс латентность | ~50 байт |

Раньше: каждое событие → полный `get-devices` (11 КБ). Теперь событие
применяется напрямую. Baseline-трафик при poll 120 с ≈ 8 МБ/сут (был 16 МБ
при 60 с), события почти бесплатны.

## Статус: v2.1 развёрнут и работает (2026-06-24)

Push с **прямым применением decoded-событий**, запись из HA
(`light.turn_on/off`), стабильное соединение без ping-timeout. Внешнее
изменение лампы отражается в HA за ~3 с. 17 сущностей.

### v1 — чтение (2026-06-24)

На `home-popovych` (HA 2026.6, Python 3.14). 17 сущностей читают живые
значения (`sensor.411317ac_1_39` = 24.12 °C, `light.411317ac_1_1` Spotai = off).
Деплой — локально по SSH (порт 11022, см. `access/home-popovych.md`).

### Находка: websockets без `.open` (Python 3.14)

HA 2026.6 ставит новую реализацию `websockets` (`ClientConnection`) — у неё
**нет** атрибута `.open` (был в legacy). Проверка живости соединения через
`.open` падала с `AttributeError` на каждом poll после первого. Решение:
не проверять `.open`, а переподключаться при любом исключении send/recv.

### Находка: блокирующее создание SSL-контекста

`websockets.connect(wss://...)` строит SSL-контекст синхронно
(`set_default_verify_paths`) — HA ругается на blocking call в event loop.
Решение: контекст создаётся через `hass.async_add_executor_job(client_context)`
и передаётся в клиент параметром `ssl_context`.

## Карта маппинга типов Larnitech → HA

Источники: вики `wiki.larnitech.com` (типы и под-типы) + живой `get-devices`
со стенда. У устройства есть `type` и `sub-type`. Часть типов — **generic**:
домен HA выбирается по `sub-type` (`lamp`, `virtual`). ✅ = сделано в v1.

### Актуаторы

| type           | sub-type         | HA-домен     | Комментарий                             |
| -------------- | ---------------- | ------------ | --------------------------------------- |
| `lamp`         | —                | `light`      | ✅ светильник on/off                     |
| `lamp`         | `socket`         | `switch`     | розетка (`device_class=outlet`)         |
| `lamp`         | `lock`           | `lock`       | замок/защёлка                           |
| `lamp`         | `air-fan`        | `fan`        | вытяжка/вентилятор on/off               |
| `lamp`         | `pump`           | `switch`     | насос                                   |
| `lamp`         | `valve-3`        | `valve`      | 3-ходовой клапан (fallback `switch`)    |
| `lamp`         | `damper`         | `valve`      | заслонка (fallback `switch`)            |
| `lamp`         | `dehumidifier`   | `humidifier` | осушитель (`device_class=dehumidifier`) |
| `lamp`         | `closing-switch` | `switch`     | импульсный доводчик                     |
| `dimmer-lamp`  | —                | `light`      | яркость (`brightness`)                  |
| `rgb-lamp`     | —                | `light`      | RGB (`rgb`/`rgbw`)                      |
| `light-scheme` | —                | `scene`      | световая сцена (или `button`)           |

### Климат

| type | sub-type | HA-домен | Комментарий |
|---|---|---|---|
| `valve-heating` | — | `climate` | термоклапан радиатора, setpoint + режимы |
| `valve-heating` | `warm-floor` | `climate` | тёплый пол |
| `fancoil` | — | `climate` | фанкойл (heat/cool + fan) |
| `conditioner` | — | `climate` | кондиционер (fan/cool/dry/heat/auto) |
| `AC` | — | `climate` | кондиционер (9-байтный статус) |
| `climate-control` | — | `climate` | термостат (setpoint-heat/cool, auto) |
| `valve` | — | `valve` | клапан открыть/закрыть (fallback `switch`) |
| `ventilation` | — | `fan` | приточная вентиляция (скорости) |
| `vent` | — | `fan` | вентиляция on/off + мощность |

### Шторы / приводы

| type | sub-type | HA-домен | Комментарий |
|---|---|---|---|
| `blinds` | — | `cover` | рулонные/жалюзи по позиции (`device_class=shade`) |
| `jalousie` | — | `cover` | жалюзи с поворотом ламелей (`tilt`, `blind`) |
| `gate` | — | `cover` | ворота (`device_class=gate`) |

### Сенсоры

| type | sub-type | HA-домен | Комментарий |
|---|---|---|---|
| `temperature-sensor` | — | `sensor` | ✅ температура, `°C` |
| `humidity-sensor` | — | `sensor` | влажность `%` (`humidity`) |
| `co2-sensor` | — | `sensor` | CO₂ `ppm` (`carbon_dioxide`) |
| `illumination-sensor` | — | `sensor` | освещённость `lx` (`illuminance`) |
| `current-sensor` | — | `sensor` | ток `A` (`current`) |
| `motion-sensor` | — | `binary_sensor` | движение (`device_class=motion`) |
| `door-sensor` | — | `binary_sensor` | открытие (`door`/`opening`) |
| `leak-sensor` | — | `binary_sensor` | протечка (`moisture`) |

### Входы / кнопки

| type | sub-type | HA-домен | Комментарий |
|---|---|---|---|
| `switch` | — | `event` | физическая кнопка: нажатие/удержание (2 байта), **не** реле |
| `security-card-reader` | — | `event` | скан карты |
| `ir-receiver` | — | `event` | принятый IR-код |
| `ir-transmitter` | — | `remote` | отправка IR (или набор `button`) |

### Virtual (generic — домен по `sub-type`)

| type | sub-type | HA-домен | Комментарий |
|---|---|---|---|
| `virtual` | `sensor` | `sensor` | числовой виртуальный сенсор |
| `virtual` | `text` | `sensor` | короткий текстовый статус |
| `virtual` | `long-text` | `sensor` | длинный текст (отдельный module ID) |
| `virtual` | `prf` | `sensor` | форматированный статус (sprintf) |
| `virtual` | `lamp` | `light` | виртуальная лампа (статус через скрипт) |
| `virtual` | `dimer-lamp` | `light` | виртуальный диммер |
| `virtual` | `rgb-lamp` | `light` | виртуальный RGB |
| `virtual` | `jalousie` / `jalousie120` | `cover` | виртуальные жалюзи |
| `virtual` | `gate` / `gate120` | `cover` | виртуальные ворота |
| `virtual` | `sunrise` | `sensor` | время рассвета/заката (timestamp) |
| `virtual` | `plan` | — | план помещения (картинка) → не сущность |
| `virtual` | `btunreg` | — | служебное → не сущность |
| `formatted virtual` | `prf` | `sensor` | форматированный текст-статус |

### Прочее (нет прямого домена)

| type | sub-type | HA-домен | Комментарий |
|---|---|---|---|
| `camera` (`rtsp`) | — | `camera` | RTSP-поток |
| `speaker` | — | `media_player` | мультирум-аудио (ограниченно) |
| `intercom` | — | — | домофон: нет единого домена (camera+lock по частям) |
| `remote-control` | — | — | виртуальный пульт (UI), не физическая сущность |
| `item-ref` | — | — | ссылка на другой item |
| `com-port` | — | — | сырой serial |
| `json` | — | — | служебный JSON-агрегат → диагностика/skip |

**Generic-логика в коде:** для `lamp` и `virtual` выбираем домен по `sub-type`
из словаря; отсутствие `sub-type` у `lamp` = `light`. Неизвестный `sub-type`
→ лог + пропуск (не создаём «не туда»).
