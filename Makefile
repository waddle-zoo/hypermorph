.PHONY: up up-demo ollama-models up-datahub playground-contexts demo-bootstrap-superset demo-bootstrap-usage demo-generate-export demo-generate-evidence demo-generate-usage-evidence demo-drift-apply demo-capture-baseline demo-capture-drift demo-drift-restore datahub-seed datahub-generate-evidence datahub-drift-apply datahub-drift-restore connection-live connection-datahub sync-live sync context-add context-sync context-status process eval playground-ui serve status down reset

# Core Hyperset stack: Postgres + migrations. No cloud/model credential required.
up:
	docker compose up -d postgres
	docker compose run --rm hyperset-migrate

# Build once, then launch the complete playground stack from one command.
# The API serves the public playground (chat) at /playground/, the reviewer
# surface at /review/, and admin settings at /admin/; no second host process
# exists.
# The scheduled benchmark still uses pinned Ollama models; the deployed demo does not.
ollama-models:
	@command -v ollama >/dev/null 2>&1 || { echo "ERROR: 'ollama' is not on PATH. Install Ollama (https://ollama.com) for the scheduled local-model benchmark." >&2; exit 1; }
	ollama pull nomic-embed-text

up-demo:
	docker compose build hyperset-migrate
	$(MAKE) up
	docker compose --profile demo up -d --wait superset
	docker compose --profile demo run --rm superset-demo-bootstrap
	# Observe the hermetic bundle BEFORE syncing the context, so the revenue
	# manifest's bi_override corroborates against the observed dataset -- the
	# evidence ref the drift processor reads. The reverse order records no ref and
	# the processor finds nothing (hy-y1ng8; tests/postgres/test_demo_processor_finding.py).
	$(MAKE) playground-observed
	$(MAKE) playground-contexts
	# One real source change through the REAL offline processor: re-observe the
	# drifted bundle on the same connection and open a real Finding plus the
	# idempotent human ReviewTask the review queue serves (hy-y1ng8).
	$(MAKE) playground-finding
	# Compose gives exported shell variables precedence over .env. Keep the
	# local .env credential authoritative for the hosted model path.
	env -u OPENAI_API_KEY docker compose --env-file .env -f docker-compose.yml -f docker-compose.demo.yml --profile demo up -d --wait api mcp-http
	@echo "Chat playground: http://localhost:$${HYPERSET_API_PORT:-8000}/playground/"
	@echo "Review:         http://localhost:$${HYPERSET_API_PORT:-8000}/review/"
	@echo "Admin settings: http://localhost:$${HYPERSET_API_PORT:-8000}/admin/"
	@echo "MCP (HTTP):     http://localhost:$${HYPERSET_MCP_HTTP_PORT:-8010}/mcp  (Streamable HTTP; connect a fresh MCP client here)"

# Build the ignored local Git checkout used by the two playground examples,
# then snapshot both domains into Hyperset's database.
playground-contexts:
	python3 playground/bootstrap_contexts.py
	@for domain in revenue supply_chain; do \
		source_id=$$(docker compose run --rm -T hyperset-migrate context add \
			--repository /repo/.runtime/playground-contexts --ref main \
			--path examples/$$domain --display-name "Playground: $$domain"); \
		docker compose run --rm -T hyperset-migrate context sync $$source_id; \
	done

# Sync a HERMETIC observed estate -- a checked-in Superset export bundle, no live
# Superset and no keys -- so a clean up-demo demonstrates a REAL reconciliation conflict
# through the genuine resolve path (hy-u26p). The revenue manifest prohibits raw_payments
# (bi_override dataset 6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4), and this bundle carries that
# dataset PLUS a chart that queries it, so `prohibited_but_referenced` fires in
# linked_evidence.conflicts. It is the SAME fixture tests/postgres/test_context_bundle.py
# proves yields the conflict, and tests/unit/test_demo_conflict_wiring.py binds this target
# to it.
#
# The bundle is staged into .runtime/observed-bundle.zip -- the connection's
# config_ref points there -- so `playground-finding` can re-observe a DRIFTED
# re-export of the SAME estate on the SAME connection (hy-y1ng8), exactly as a
# real Superset re-export would change one metric under a stable connection.
# At this point the approved dataset's metric MATCHES the manifest (no drift):
# the finding is the later re-export, not this observation.
playground-observed:
	mkdir -p .runtime
	cp tests/fixtures/superset/6.1.0/usage/official-export.zip .runtime/observed-bundle.zip
	conn=$$(docker compose run --rm -T hyperset-migrate connections create-superset-bundle \
		--path /repo/.runtime/observed-bundle.zip \
		--display-name "Playground: observed (Superset bundle)"); \
	docker compose run --rm -T hyperset-migrate sync run $$conn

# The finding + review-task demo half (hy-y1ng8): re-export the SAME observed
# estate with one metric drifted (official-export-drift.zip changes the approved
# finance_orders_daily `recognized_revenue` metric from SUM(gross_amount -
# tax_amount) to SUM(gross_amount)), re-observe it on the SAME connection so it
# is ONE `updated` change of the corroborated asset, then run the REAL offline
# processor over that sync. The rule (approved_expression_drift) opens one
# explainable Finding and one idempotent human ReviewTask -- the queue
# `list_review_tasks` serves. NOT direct-seeded: the row is what the real
# pipeline produced from a real source change (prior mayor ruling). No hosted
# keys; deterministic. The generic `process:` stub stays as-is (hy-jp0gq owns
# the sync-run-id source for it); the demo calls the CLI directly, as the ruling
# permits.
playground-finding:
	cp tests/fixtures/superset/6.1.0/usage/official-export-drift.zip .runtime/observed-bundle.zip
	conn=$$(docker compose run --rm -T hyperset-migrate connections list \
		| awk -F'\t' '$$3 == "Playground: observed (Superset bundle)" { print $$1; exit }'); \
	run=$$(docker compose run --rm -T hyperset-migrate sync run $$conn \
		| sed -n 's/^sync_run_id=\([^ ]*\).*/\1/p'); \
	docker compose run --rm -T hyperset-migrate process sync $$run

migrate:
	docker compose run --rm hyperset-migrate

# Idempotent: re-running does not create duplicate database/dataset entries.
demo-bootstrap-superset:
	docker compose --profile demo run --rm superset-demo-bootstrap

# Seeds charts and one dashboard onto the datasets `demo-bootstrap-superset`
# already created, so a dataset's use is observable at all. Idempotent, and
# additive: it creates no dataset, so the revenue capture's own payloads are
# unchanged by running it.
demo-bootstrap-usage:
	docker compose --profile demo run --rm superset-usage-bootstrap

# Generates the checked-in chart/dashboard reference evidence from real
# Superset: one dashboard export ZIP plus the chart and dashboard REST bodies
# 6.1.0 really serves -- identity and both references included, under different
# field names than the export carries. The connector reads both spellings
# (hy-rt4v), so this is the pin under live REST normalization, not a capture
# waiting for a slice.
demo-generate-usage-evidence:
	docker compose --profile demo run --rm usage-evidence generate

# Generates a real official export ZIP from the running pinned Superset
# instance via its own export REST API -- never hand-authored.
demo-generate-export:
	docker compose --profile demo run --rm demo-export

# Generates the checked-in Gate A evidence from real Superset, applies exactly
# one metric-expression drift, captures it, restores baseline, and verifies the
# controlled-property hash.
demo-generate-evidence:
	docker compose --profile demo run --rm demo-evidence generate

# --no-deps: demo-evidence depends on the bootstrap service, and re-running
# that rewrites every seeded dataset -- which would make this more than one
# controlled change.
demo-drift-apply:
	docker compose --profile demo run --rm --no-deps demo-evidence apply

demo-capture-baseline:
	docker compose --profile demo run --rm demo-evidence capture-baseline

demo-capture-drift:
	docker compose --profile demo run --rm demo-evidence capture-drift

demo-drift-restore:
	docker compose --profile demo run --rm --no-deps demo-evidence restore

# Core stack + the real pinned DataHub OSS v1.6.0 source environment, seeded
# with the revenue evidence ADR 0010 asks DataHub to supply. GMS boots in
# ~2 minutes behind its SystemUpdate job.
up-datahub: up
	docker compose --profile datahub up -d datahub-gms
	docker compose --profile datahub run --rm datahub-seed seed

datahub-seed:
	docker compose --profile datahub run --rm datahub-seed seed

# Records the real GraphQL bodies for the connector's own projections, for
# both the baseline and the one drifted glossary definition. Restoring
# reproduces the baseline byte for byte, so no third capture is written.
datahub-generate-evidence:
	docker compose --profile datahub run --rm datahub-evidence baseline
	docker compose --profile datahub run --rm --no-deps datahub-seed apply
	docker compose --profile datahub run --rm --no-deps datahub-evidence drift
	docker compose --profile datahub run --rm --no-deps datahub-seed restore

# --no-deps: datahub-evidence/datahub-seed depend on the seed job, and
# re-running that would rewrite every seeded aspect -- which would make this
# more than one controlled change.
datahub-drift-apply:
	docker compose --profile datahub run --rm --no-deps datahub-seed apply

datahub-drift-restore:
	docker compose --profile datahub run --rm --no-deps datahub-seed restore

# Register a live GraphQL connection against the pinned DataHub and print its id.
connection-datahub:
	docker compose run --rm hyperset-migrate connections create-datahub \
		--base-url http://datahub-gms:8080

# Register a live REST connection against the demo Superset and print its id.
connection-live:
	docker compose run --rm hyperset-migrate connections create-superset-rest \
		--base-url http://superset:8088

# Usage: make sync-live CONNECTION_ID=conn-xxx
# Credentials are passed for the life of the process only -- never persisted
# on the connection row.
sync-live:
ifndef CONNECTION_ID
	$(error CONNECTION_ID is required, e.g. make sync-live CONNECTION_ID=conn-xxx. \
Create one first: make connection-live)
endif
	docker compose run --rm \
		-e HYPERSET_SUPERSET_USERNAME=$${HYPERSET_SUPERSET_USERNAME:-$${SUPERSET_ADMIN_USERNAME:-admin}} \
		-e HYPERSET_SUPERSET_PASSWORD=$${HYPERSET_SUPERSET_PASSWORD:-$$SUPERSET_ADMIN_PASSWORD} \
		hyperset-migrate sync run $(CONNECTION_ID)

# Usage: make sync CONNECTION_ID=conn-xxx
sync:
ifndef CONNECTION_ID
	$(error CONNECTION_ID is required, e.g. make sync CONNECTION_ID=conn-xxx. \
Create one first: docker compose run --rm hyperset-migrate connections create-superset-bundle --path demo/revenue/superset/export.zip)
endif
	docker compose run --rm hyperset-migrate sync run $(CONNECTION_ID)

# Usage: make context-add REPOSITORY=/repos/analytics CONTEXT_PATH=playground/examples/revenue [REF=main]
# The repository must be reachable from inside the container (a URL, or a
# path mounted into it). Nothing is written back to it: Git stays the
# authority, Hyperset records the exact commit.
context-add:
ifndef REPOSITORY
	$(error REPOSITORY is required, e.g. make context-add REPOSITORY=https://host/org/repo.git CONTEXT_PATH=playground/examples/revenue)
endif
ifndef CONTEXT_PATH
	$(error CONTEXT_PATH is required, e.g. CONTEXT_PATH=playground/examples/revenue)
endif
	docker compose run --rm hyperset-migrate context add \
		--repository $(REPOSITORY) --ref $${REF:-main} --path $(CONTEXT_PATH)

# Usage: make context-sync SOURCE_ID=ctxsrc-xxx
# An unchanged commit is a no-op; an invalid commit leaves the last valid
# snapshot serving and exits non-zero.
context-sync:
ifndef SOURCE_ID
	$(error SOURCE_ID is required, e.g. make context-sync SOURCE_ID=ctxsrc-xxx. \
Create one first: make context-add)
endif
	docker compose run --rm hyperset-migrate context sync $(SOURCE_ID)

# Git context sync health: configured repo/ref/path, current commit, last attempt.
context-status:
	docker compose run --rm hyperset-migrate context status

# Run the offline processor over the most-recent COMPLETED sync run (hy-jp0gq).
# `sync latest` is the deterministic source for "which sync run" the generic
# target lacked; the demo's up-demo captures a specific sync_run_id and calls
# `process sync` directly (hy-y1ng8), and this uses the SAME CLI for the ad-hoc
# operator case.
#
# The lookup's EXIT STATUS is inspected before its stdout, because both a genuine
# "no sync yet" and a FAILED lookup (DB unreachable, `hyperset-migrate` crash,
# docker daemon down) leave `$$run` empty (critic/adversary #508). Only a clean
# exit-0-with-empty-stdout is the benign no-op; a nonzero lookup propagates its
# own nonzero exit so `make process` never falsely reports a healthy no-op. Status
# is captured with `$$?` on the line right after the assignment -- no pipe -- so it
# is the substitution's own status, not a later command's.
process:
	@run=$$(docker compose run --rm -T hyperset-migrate sync latest); status=$$?; \
	if [ $$status -ne 0 ]; then \
		echo "make process: could not look up the latest sync run (sync latest exited $$status) -- is the database/stack up? Not processing." >&2; \
		exit $$status; \
	elif [ -n "$$run" ]; then \
		echo "make process: processing latest completed sync run $$run"; \
		docker compose run --rm -T hyperset-migrate process sync $$run; \
	else \
		echo "make process: no completed sync run yet -- run 'make up-demo' (or 'hyperset sync run <connection>'), then re-run 'make process'"; \
	fi

eval:
	@echo "make eval: blocked on #25 (evaluator not implemented yet)" >&2
	@exit 1

# Build the playground UI bundle into playground/ui/dist/ (gitignored). Required on a
# CLEAN CHECKOUT before scripts/gate.py: without it the API serves the SOURCE
# playground/ui/index.html (which references /src/main.jsx) instead of the built
# /playground/assets/... bundle, and tests/unit/transport/test_http.py's served-
# playground test fails as a broken assertion rather than a missing step (hy-r8jd / #346).
playground-ui:
	@command -v npm >/dev/null 2>&1 || { echo "ERROR: 'npm' is not on PATH. Install Node.js (https://nodejs.org) so the playground UI bundle can be built; the served-playground HTTP test needs it." >&2; exit 1; }
	cd playground/ui && npm ci && npm run build

# resolve_analytics_context + validate_analytics_plan over HTTP. The same two
# operations over MCP stdio are spawned per client:
#   docker compose run --rm -T mcp
serve:
	docker compose up -d --wait api
	@echo "hyperset api on localhost:$${HYPERSET_API_PORT:-8000} (POST /v0/resolve_analytics_context)"

status:
	docker compose ps
	@echo ""
	@echo "Runnable:"
	@echo "  process -- offline processor over the latest completed sync run (make process)"
	@echo "Not yet runnable here (no service/CLI defined -- code doesn't exist yet):"
	@echo "  review-ui -- #39   eval -- #25"

# Preserves volumes (state survives) -- see `reset` for the destructive version.
down:
	docker compose --profile demo down

# Explicitly destructive: drops every named volume (Hyperset, Superset,
# and analytics data). Requires typed confirmation, same pattern as
# `hyperset db reset --yes`.
reset:
	@echo "This will DESTROY all Hyperset, Superset, and analytics data (all volumes)."
	@read -p "Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted." && exit 1)
	docker compose --profile demo down -v
