# `/api/render_video` — інструкція

Ендпоінт рендерить відео з карти (зображення) та фонового відео на сервері
через `ffmpeg`. Використовується сторінкою Video Studio (`templates/video.html`).

## Метод

```
POST /api/render_video
Content-Type: application/json
```

## Що приймає (Request body, JSON)

```jsonc
{
  "map_file": "data:image/png;base64,....",
  "background_video": "data:video/mp4;base64,....",
  "video_title": "My awesome map video",
  "codec": "libx264"
}
```

| Поле               | Тип    | Опис |
|--------------------|--------|------|
| `map_file`         | string | Зображення карти у base64 (data URL `data:image/...;base64,...`). |
| `background_video` | string | Фонове відео у base64 (data URL `data:video/...;base64,...`). |
| `video_title`      | string | Заголовок відео (використовується у назві результату). |
| `codec`            | string | Один з: `libx264`, `h264_nvenc`, `h264_videotoolbox`. |

### Дозволені кодеки

| Значення             | Для чого |
|----------------------|----------|
| `libx264`            | Універсальний програмний енкодер (працює всюди). |
| `h264_nvenc`         | Апаратне кодування на відеокартах NVIDIA. |
| `h264_videotoolbox`  | Апаратне кодування на чипах Apple Silicon. |

## Обмеження

- Кожен вхідний матеріал (`map_file`, `background_video`) — **не більше 10 MB**
  після декодування з base64. Більші файли відхиляються з помилкою.
- `codec` має бути одним із дозволених значень, інакше — помилка.

## Що віддає (Response, JSON)

### Успіх (HTTP 200)

```jsonc
{
  "status": "success",
  "file_path": "/tmp/automap_storage/render/my_awesome_map_video.mp4",
  "download_url": "/api/download_video?path=%2Ftmp%2Fautomap_storage%2Frender%2Fmy_awesome_map_video.mp4"
}
```

| Поле           | Опис |
|----------------|------|
| `status`       | `"success"`. |
| `file_path`    | Шлях до готового відео **на сервері**. |
| `download_url` | (необов'язково) URL для завантаження файлу. Якщо відсутній — клієнт використає `/api/download_video?path=<file_path>`. |

### Помилка (HTTP 4xx / 5xx)

```jsonc
{
  "status": "error",
  "error": "Human readable error message"
}
```

Типові причини: відсутнє обов'язкове поле, файл > 10 MB, непідтримуваний
кодек, помилка `ffmpeg`.

## Завантаження результату

```
GET /api/download_video?path=<file_path>
```

Віддає файл за вказаним серверним шляхом (`file_path` з відповіді вище).

## Приклад (curl)

```bash
MAP_B64=$(base64 -i map.png)
BG_B64=$(base64 -i background.mp4)

curl -X POST http://localhost:8080/api/render_video \
  -H "Content-Type: application/json" \
  -d "{
        \"map_file\": \"data:image/png;base64,${MAP_B64}\",
        \"background_video\": \"data:video/mp4;base64,${BG_B64}\",
        \"video_title\": \"My awesome map video\",
        \"codec\": \"libx264\"
      }"
```

## Примітки для реалізації на бекенді

1. Приймати JSON, діставати base64 з `data:...;base64,` префікса (або якщо
   префікса немає — трактувати рядок як чистий base64).
2. Декодувати у тимчасові файли (наприклад у `/tmp/automap_storage/render/`).
3. Перевіряти розмір кожного декодованого файлу — **≤ 10 MB**.
4. Валідувати `codec` за списком дозволених.
5. Запускати `ffmpeg` з відповідним `-c:v <codec>`.
6. Повертати `status`, `file_path` (і за бажанням `download_url`).
