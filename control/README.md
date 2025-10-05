# Control service
Микросервис Control (FastAPI + PostgreSQL) собирает почасовые ряды из сервиса «Теплицы», чистит данные (выбросы/пропуски), считает статистику по теплицам/регионам и (опц.) вызывает сервис «Оценка» для статуса 0/1/2.

Что внутри

control — основной сервис (FastAPI, SQLModel/SQLAlchemy, httpx)

greenhouse-mock — эмулятор «Теплицы» (Basic-Auth)

state-mock — эмулятор «Оценки» (Basic-Auth)

postgres — БД

Запуск:
cp .env.example .env
docker compose up -d --build
docker compose ps

Swagger:

Control: http://localhost:8000/docs

Greenhouse-mock: http://localhost:8101/docs

State-mock: http://localhost:8102/docs

В моках нажмите Authorize и введите креды из .env.

Конфигурация (.env)
DATABASE_URL=postgresql+psycopg2://control_user:control_pass@db:5432/control_db
GH_USERNAME=greenhouse_user
GH_PASSWORD=greenhouse_pass
STATE_USERNAME=state_user
STATE_PASSWORD=state_pass
GREENHOUSE_BASE=http://greenhouse-mock:8000
STATE_BASE=http://state-mock:8000


Структура:
control/src/ (эндпоинты, модели, очистка, клиенты)
greenhouse-mock/src/ (API эмулятора «Теплицы»)
state-mock/src/ (API эмулятора «Оценки»)
docker-compose.yml, .env(.example)
