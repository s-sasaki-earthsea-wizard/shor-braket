.DEFAULT_GOAL := help
SHELL := /bin/bash

# ---- Secrets and environment ----
# Load .env if present and export everything to sub-processes.
# .env is gitignored; see .env.example for the template.
ifneq (,$(wildcard .env))
include .env
export
endif

# ---- Default parameters ----
N      ?= 6
A      ?=
T      ?=
SHOTS  ?= 1000
DEVICE ?= $(if $(BRAKET_DEFAULT_DEVICE),$(BRAKET_DEFAULT_DEVICE),sv1)
ORACLE ?= generic-repeated

export N A T SHOTS DEVICE ORACLE

# Guard for targets whose implementation has not landed yet.
# Fails loudly instead of silently doing nothing.
define notimpl
	@echo ""
	@echo "  [not implemented] $(1)"
	@echo "  See: $(2)"
	@echo ""
	@exit 1
endef

include makefiles/docker.mk
include makefiles/setup.mk
include makefiles/quality.mk
include makefiles/sim.mk
include makefiles/braket.mk
include makefiles/infra.mk

.PHONY: help
help:  ## このヘルプを表示する
	@echo ""
	@echo "  shor-braket — Shor's algorithm on local simulator and Amazon Braket"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' Makefile makefiles/*.mk \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  変数:  N=$(N)  A=$(A)  T=$(T)  SHOTS=$(SHOTS)  DEVICE=$(DEVICE)  ORACLE=$(ORACLE)"
	@echo ""
ifneq (,$(wildcard .env))
	@echo "  .env:  読み込み済み (AWS_ACCOUNT_ID / BRAKET_RESULTS_BUCKET などは .env から取得)"
else
	@echo "  .env:  未作成 (ローカル開発には不要。AWS 利用時は cp .env.example .env)"
endif
	@echo ""
