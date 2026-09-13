# Run local development tools with the host user's file ownership.
LOCAL_UID := $(shell id -u)
LOCAL_GID := $(shell id -g)
export LOCAL_UID LOCAL_GID

COMPOSE := docker compose --env-file /dev/null -f docker/docker-compose.yml
LOCAL_RUN = $(COMPOSE) run --rm --build local

.PHONY: shell
shell:  ## Docker の開発環境で bash を開く (終了は exit)
	$(LOCAL_RUN) bash

.PHONY: docker-down
docker-down:  ## このプロジェクトの Compose コンテナとネットワークを片付ける
	$(COMPOSE) down --remove-orphans
