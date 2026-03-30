PYTHON ?= python3
WEB_PORT ?= 5000

.PHONY: install run test up down logs restart migrate reindex health set-role admin agente piloto create-admin

install:
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) app.py

test:
	$(PYTHON) -m unittest discover tests -v

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f web db

restart:
	docker compose down
	docker compose up --build

migrate:
	$(PYTHON) migration_service.py

set-role:
	$(PYTHON) scripts/set_user_role.py $(EMAIL) $(ROLE)

admin:
	$(PYTHON) scripts/set_user_role.py $(EMAIL) admin

create-admin:
	$(PYTHON) scripts/create_admin.py $(EMAIL) $(PASSWORD)

agente:
	$(PYTHON) scripts/set_user_role.py $(EMAIL) agente

piloto:
	$(PYTHON) scripts/set_user_role.py $(EMAIL) piloto

reindex:
	curl -fsS -X POST http://127.0.0.1:$(WEB_PORT)/knowledge/reindex

health:
	curl -fsS http://127.0.0.1:$(WEB_PORT)/healthz
