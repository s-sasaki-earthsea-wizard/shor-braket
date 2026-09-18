# Run local development tools with the host user's file ownership.
LOCAL_UID := $(shell id -u)
LOCAL_GID := $(shell id -g)
export LOCAL_UID LOCAL_GID

COMPOSE := docker compose --env-file /dev/null -f docker/docker-compose.yml
LOCAL_RUN = $(COMPOSE) run --rm --build local

# The only runner that can reach AWS. Keep a TTY: the assumed-role profiles ask for an MFA code
# on stdin, and the credentials are mounted read-only so botocore cannot cache the answer.
AWS_RUN = $(COMPOSE) run --rm --build aws

# The aws service mounts $(HOME)/.aws; an unset HOME would silently mount the wrong path.
define require_home
	@test -n "$(HOME)" || { \
		echo ""; \
		echo "  HOME is not set, so the AWS credentials directory cannot be located."; \
		echo ""; \
		exit 1; }
	@test -d "$(HOME)/.aws" || { \
		echo ""; \
		echo "  $(HOME)/.aws does not exist. Configure the profiles first (infra/iam/README.md)."; \
		echo ""; \
		exit 1; }
endef

.PHONY: shell
shell:  ## Docker の開発環境で bash を開く (終了は exit)
	$(LOCAL_RUN) bash

.PHONY: docker-down
docker-down:  ## このプロジェクトの Compose コンテナとネットワークを片付ける
	$(COMPOSE) down --remove-orphans
