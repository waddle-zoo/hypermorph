#!/usr/bin/env bash
#
# gh-to-gastown-sync.sh
#
# Manual operator tool that pulls open GitHub issues and markdown requirements
# into Gas Town beads, then notifies the Mayor. The script retains `--loop` for
# explicit operator sessions, but this repository installs no scheduler.
#
# Dedup is NOT reimplemented here: `bd import` already upserts by ID and skips
# stale rows based on `updated_at`, so this script always sends the full
# current snapshot (all open issues, all inbox files) every run and lets bd
# decide what it writes. There is no separate state file.
#
# What this script DOES have to work out for itself is which of those rows
# were new, because bd's counts cannot say (hy-1lou): `created` counts every
# accepted row, including rows that only rewrote or tied with a bead that
# already existed. The ids bd reports, compared with the ids the rig held
# before the pass, are what makes "new" true.
#
# Requires: gh (authenticated), bd, gt, jq
#
# Usage:
#   ./scripts/gh-to-gastown-sync.sh                # run once
#   ./scripts/gh-to-gastown-sync.sh --dry-run      # inspect what would happen
#   ./scripts/gh-to-gastown-sync.sh --config path/to/.gastown-watcher.yml
#   ./scripts/gh-to-gastown-sync.sh --loop         # run forever with INTERVAL sleep
#
# Configuration can come from env vars or a YAML config file.
# See .gastown-watcher.yml.example for supported keys.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The repository root, one level up now that this lives in scripts/ (hy-gh-99).
# The default config still sits at the root, so this is the path that finds it.
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE=""
DRY_RUN=0
LOOP_MODE=0
TRANSIENT_ERRORS=0
BEAD_PREFIX=""
# Where the GitHub half records "<bead> depends on <bead>" for the dependency
# pass that runs after the import, since an edge needs both beads to exist.
DEPENDENCY_FILE=""
# Every bead id the rig already holds, in any status, and the subset intake
# must leave alone. Both are read once per pass by `fetch_bead_states`.
KNOWN_BEAD_IDS=""
SETTLED_BEAD_IDS=""
# The ids this pass actually created, which is what the Mayor is nudged about.
NEW_BEAD_IDS=""
# Set before load_yaml_config runs so log() (e.g. its own "unknown config
# key" warning) never hits an unbound-variable error under set -u before
# config_defaults gives LOG_FILE its real value.
LOG_FILE=""

usage() {
  cat <<EOF
Usage: $0 [--config FILE] [--dry-run] [--loop] [--help]

Options:
  --config FILE   Load settings from a YAML config file before env vars.
  --dry-run       Do not change anything; show what would be imported.
  --loop          Run forever and sleep INTERVAL seconds between passes.
  --help          Show this message.
EOF
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --config)
        shift
        CONFIG_FILE="$1"
        ;;
      --dry-run|-n)
        DRY_RUN=1
        ;;
      --loop)
        LOOP_MODE=1
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
    esac
    shift
  done
}

load_yaml_config() {
  if [ -z "$CONFIG_FILE" ]; then
    CONFIG_FILE="$REPO_ROOT/.gastown-watcher.yml"
  fi
  if [ ! -f "$CONFIG_FILE" ]; then
    return 0
  fi

  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    local line key value
    line="${raw_line%%#*}"
    line="${line%$'\r'}"
    line="${line#"${line%%[^[:space:]]*}"}"
    line="${line%"${line##*[^[:space:]]}"}"
    [ -z "$line" ] && continue

    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*:[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="${value%\"}"
      value="${value#\"}"
      value="${value%'}"
      value="${value#'}"
      value="${value//\$HOME/$HOME}"
      value="${value/#\~/$HOME}"
      case "$key" in
        GH_REPO|RIG_NAME|RIG_PATH|LABEL_FILTER|INBOX_DIR|LOG_FILE|INTERVAL)
          if [ -z "${!key:-}" ]; then
            printf -v "$key" '%s' "$value"
          fi
          ;;
        *)
          log "WARNING: unknown config key '$key' in $CONFIG_FILE; ignoring."
          ;;
      esac
    fi
  done < "$CONFIG_FILE"
}

ensure_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command '$1' not found on PATH" >&2
    exit 1
  }
}

log_file_append() {
  [ -z "$LOG_FILE" ] && return
  local msg="$1"
  local ts
  ts="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  printf '%s %s\n' "$ts" "$msg" >> "$LOG_FILE"
}

log() {
  local msg="$1"
  printf '%s\n' "$msg"
  log_file_append "$msg"
}

hash_string() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 1 | awk '{print $1}'
  elif command -v sha1sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha1sum | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    printf '%s' "$1" | openssl sha1 | awk '{print $2}'
  else
    log "ERROR: no SHA1 hash command available (shasum, sha1sum, or openssl required)"
    exit 1
  fi
}

sanitize_slug() {
  local raw="$1"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  raw="$(printf '%s' "$raw" | tr -c '[:alnum:]_-' '-')"
  raw="${raw##-}"
  raw="${raw%%-}"
  raw="$(printf '%s' "$raw" | sed 's/-\{2,\}/-/g')"
  if [ -z "$raw" ]; then
    raw="$(hash_string "$1" | cut -c1-10)"
  fi
  printf '%s' "$raw"
}

config_defaults() {
  GH_REPO="${GH_REPO:-}"
  RIG_NAME="${RIG_NAME:-hyperset}"
  RIG_PATH="${RIG_PATH:-$HOME/gt/$RIG_NAME}"
  LABEL_FILTER="${LABEL_FILTER:-}"
  INBOX_DIR="${INBOX_DIR:-requirements/inbox}"
  LOG_FILE="${LOG_FILE:-$HOME/.gastown-watcher.log}"
  INTERVAL="${INTERVAL:-1800}"
}

prepare_environment() {
  ensure_command gh
  ensure_command bd
  ensure_command gt
  ensure_command jq
  mkdir -p "$(dirname "$LOG_FILE")"
  mkdir -p "$INBOX_DIR"
  touch "$LOG_FILE"
}

# gt routes `gt sling` lookups by bead ID prefix (e.g. "hy-") to the owning
# rig database. A bead ID with an unrecognized prefix (our own "gh-"/"md-"
# convention) never gets queried at all, so sling reports it as "not found"
# even though bd can see it fine. Embedding the town's real prefix keeps our
# deterministic per-source suffix (for dedup) while making the ID routable.
resolve_bead_prefix() {
  BEAD_PREFIX=""
  if [ -z "$RIG_PATH" ] || [ ! -d "$RIG_PATH" ]; then
    return
  fi
  local prefix
  prefix="$(cd "$RIG_PATH" && bd where --json 2>/dev/null | jq -r '.prefix // empty')"
  if [ -n "$prefix" ]; then
    BEAD_PREFIX="$prefix"
  fi
}

# One `bd list` answers both questions this script asks of the rig, so a pass
# reads the database once.
#
# ONLY AN OPEN BEAD MAY BE REWRITTEN. bd import defaults a missing OR present
# status field to "open" - there is no way to send an update that leaves
# status alone - so re-sending a bead rewrites its status. Both harms are
# measured, not feared: a bead actively hooked to a crew/polecat got silently
# reset to open + unassigned the moment its source GitHub issue changed, and
# a CLOSED bead re-sent from a touched source comes back as open, reported by
# bd itself as "status closed -> open" in `updated_issues` (hy-1lou). Closing
# the bead is therefore what retires an inbox file: intake never revives it,
# and the file stays on disk as the record (requirements/inbox/README.md).
fetch_bead_states() {
  KNOWN_BEAD_IDS=""
  SETTLED_BEAD_IDS=""
  if [ -z "$RIG_PATH" ] || [ ! -d "$RIG_PATH" ]; then
    return
  fi
  local rows
  # --all because closed beads are exactly the ones that must not be
  # resurrected and bd hides them by default; --limit 0 because bd's list
  # limit defaults to 50 and this rig is already past that.
  rows="$(cd "$RIG_PATH" && bd list --all --limit 0 --json 2>/dev/null)"
  [ -z "$rows" ] && return
  KNOWN_BEAD_IDS="$(printf '%s' "$rows" | jq -r '.[].id' 2>/dev/null)"
  SETTLED_BEAD_IDS="$(printf '%s' "$rows" | jq -r '.[] | select(.status != "open") | .id' 2>/dev/null)"
}

id_is_listed() {
  local id="$1" list="$2"
  [ -z "$list" ] && return 1
  printf '%s\n' "$list" | grep -qxF "$id"
}

is_settled_bead() {
  id_is_listed "$1" "$SETTLED_BEAD_IDS"
}

is_known_bead() {
  id_is_listed "$1" "$KNOWN_BEAD_IDS"
}

# bd's Dolt auto-commit defaults to off, so bd import writes sit in the
# local working set until committed. bd's own reads already see them, but
# bd's docs call this out as needed before other operations rely on a clean
# committed state, so flush explicitly after each import batch.
commit_pending_changes() {
  if [ -z "$RIG_PATH" ] || [ ! -d "$RIG_PATH" ]; then
    return
  fi
  local err_file
  err_file="$(mktemp /tmp/bd-dolt-commit-err.XXXXXX)"
  if ! (cd "$RIG_PATH" && bd dolt commit -m "gh-to-gastown-sync: flush pending imports" >"$err_file" 2>&1); then
    log "WARNING: bd dolt commit failed after import; gt sling may not see new/updated beads yet. $(tr '\n' ' ' < "$err_file")"
  fi
  rm -f "$err_file"
}

# The Mayor is Gas Town's own coordination point between the human and the
# agents (gt mayor --help), and per this rig's mayor directive it now owns
# ALL dispatch decisions (which bead, to hyperion, one at a time) — this
# script only imports. Nudge it (queue mode = non-interruptive, per this
# town's own "default to nudge for routine comms" convention) so it knows
# to check bd ready. Only fires when a run actually changed something.
#
# THE READINESS CLAIM IS DERIVED, NOT ASSERTED. Three consecutive passes
# summoned the Mayor with "Ready work is waiting" for ten GitHub issues that
# had been triaged days earlier (hy-1lou); an unconditional claim trains its
# reader to ignore the notification. A nudge now fires only for beads this
# pass created that are open and unassigned when the pass ends, and it names
# them, so the message is checkable by the agent receiving it.
notify_mayor() {
  if [ -z "$NEW_BEAD_IDS" ]; then
    return
  fi
  if [ -z "$RIG_PATH" ] || [ ! -d "$RIG_PATH" ]; then
    return
  fi
  local unassigned ready_ids id
  unassigned="$(cd "$RIG_PATH" && bd list --status open --no-assignee --limit 0 --json 2>/dev/null | jq -r '.[].id' 2>/dev/null)"
  ready_ids=""
  while IFS= read -r id; do
    [ -z "$id" ] && continue
    id_is_listed "$id" "$unassigned" && ready_ids="${ready_ids:+$ready_ids, }$id"
  done <<< "$NEW_BEAD_IDS"
  if [ -z "$ready_ids" ]; then
    log "Imported $(count_lines "$NEW_BEAD_IDS") new bead(s), none of them open and unassigned; not summoning the Mayor."
    return
  fi
  local msg="gh-to-gastown-sync: new bead(s) in $RIG_NAME, open and unassigned: $ready_ids. Please triage and assign."
  local err_file
  err_file="$(mktemp /tmp/gt-nudge-mayor-err.XXXXXX)"
  if (cd "$RIG_PATH" && gt nudge mayor "$msg" --mode queue) >"$err_file" 2>&1; then
    log "Notified Mayor: $msg"
  else
    log "INFO: unable to nudge Mayor. $(tr '\n' ' ' < "$err_file")"
  fi
  rm -f "$err_file"
}

# The ids bd accepted that the rig did not already hold. This is the only
# reliable reading of "new": bd's `created` count includes rows that merely
# rewrote an existing bead and rows whose `updated_at` tied (kept local,
# listed in `tie_kept_local_ids`), and a row that is newer but identical in
# content is reported as created with no `updated` at all — so no arithmetic
# over bd's numbers can separate a new bead from a rewritten one.
import_new_ids() {
  local out="$1" id
  while IFS= read -r id; do
    [ -z "$id" ] && continue
    is_known_bead "$id" || printf '%s\n' "$id"
  done <<< "$(printf '%s' "$out" | jq -r '.ids // [] | .[]' 2>/dev/null)"
}

count_lines() {
  [ -z "$1" ] && { printf '0'; return; }
  printf '%s\n' "$1" | grep -c .
}

# Runs `bd import` (or its --dry-run preview) on a JSONL file and reports what
# the pass did. bd upserts by ID and skips rows that aren't strictly newer
# than what it already has (per updated_at) — that's the entire dedup
# mechanism and it is still bd's. What this reports is which of the accepted
# ids were new, which bd's counts do not say.
run_bd_import() {
  local file="$1" label="$2"
  local out err_file rc
  err_file="$(mktemp /tmp/bd-import-err.XXXXXX)"
  if [ "$DRY_RUN" -eq 1 ]; then
    out="$(cd "$RIG_PATH" && bd import "$file" --dry-run --json 2>"$err_file")"
  else
    out="$(cd "$RIG_PATH" && bd import "$file" --json 2>"$err_file")"
  fi
  rc=$?
  local created skipped
  if [ "$rc" -ne 0 ] || ! created="$(printf '%s' "$out" | jq -r '.created // 0' 2>/dev/null)"; then
    log "WARNING: bd import failed for $label; will retry next run. $(tr '\n' ' ' < "$err_file")"
    rm -f "$err_file"
    TRANSIENT_ERRORS=1
    return
  fi
  rm -f "$err_file"
  skipped="$(printf '%s' "$out" | jq -r '.skipped // 0')"
  local new_ids new_count rewritten
  new_ids="$(import_new_ids "$out")"
  new_count="$(count_lines "$new_ids")"
  rewritten=$((created - new_count))
  if [ "$DRY_RUN" -eq 1 ]; then
    # --dry-run does not apply the staleness comparison at all: it reports
    # every row as created. Counted the same way regardless, so the preview
    # over-reports rewrites rather than inventing new beads.
    log "DRY-RUN: would import $label: $new_count new, $rewritten already held, $skipped stale-skipped."
    return
  fi
  log "Imported $label: $new_count new, $rewritten already held, $skipped stale-skipped."
  if [ -n "$new_ids" ]; then
    NEW_BEAD_IDS="${NEW_BEAD_IDS:+$NEW_BEAD_IDS
}$new_ids"
  fi
}

parse_markdown_frontmatter() {
  local file="$1"
  awk 'BEGIN { state=0; title=""; type=""; priority="" }
  NR==1 && $0 != "---" { exit 1 }
  /^---$/ {
    if (state == 0) { state=1; next }
    if (state == 1) { state=2; next }
  }
  state == 1 {
    if ($0 ~ /^[[:space:]]*title[[:space:]]*:/) {
      sub(/^[[:space:]]*title[[:space:]]*:[[:space:]]*/,"")
      title=$0
    } else if ($0 ~ /^[[:space:]]*type[[:space:]]*:/) {
      sub(/^[[:space:]]*type[[:space:]]*:[[:space:]]*/,"")
      type=$0
    } else if ($0 ~ /^[[:space:]]*priority[[:space:]]*:/) {
      sub(/^[[:space:]]*priority[[:space:]]*:[[:space:]]*/,"")
      priority=$0
    }
  }
  END {
    if (state != 2) { exit 1 }
    if (title == "") { exit 1 }
    if (type == "") { type="task" }
    if (priority == "") { priority="2" }
    printf "%s\n%s\n%s\n", title, type, priority
  }' "$file"
}

markdown_description() {
  local file="$1"
  awk 'BEGIN { state=0 }
  /^---$/ {
    if (state == 0) { state=1; next }
    if (state == 1) { state=2; next }
  }
  state == 2 { print }
  ' "$file" | sed '/^[[:space:]]*$/d'
}

github_import_record() {
  local id="$1" title="$2" description="$3" number="$4" url="$5" labels="$6" updated_at="$7"
  # bd import expects labels as a JSON array, not a comma-joined string.
  jq -nc --arg id "$id" --arg title "$title" --arg description "$description" --arg number "$number" --arg url "$url" --arg labels "$labels" --arg updated_at "$updated_at" '{id:$id, title:$title, description:$description, issue_type:"task", status:"open", priority:2, external_ref:$url, source:"github", source_ref:$number, labels:(if $labels == "" then [] else ($labels | split(",")) end), updated_at:$updated_at}'
}

markdown_import_record() {
  local id="$1" title="$2" description="$3" kind="$4" priority="$5" file="$6" updated_at="$7"
  jq -nc --arg id "$id" --arg title "$title" --arg description "$description" --arg kind "$kind" --arg priority "$priority" --arg file "$file" --arg updated_at "$updated_at" '{id:$id, title:$title, description:$description, issue_type:$kind, status:"open", priority:($priority|tonumber), external_ref:$file, source:"markdown", source_ref:$file, updated_at:$updated_at}'
}

import_github_issues() {
  if [ -z "$GH_REPO" ]; then
    log "Skipping GitHub issue scan because GH_REPO is not configured."
    return
  fi
  log "Scanning GitHub issues in $GH_REPO..."
  local gh_args=(issue list --repo "$GH_REPO" --state open --limit 1000 --json number,title,body,url,updatedAt,labels)
  if [ -n "$LABEL_FILTER" ]; then
    gh_args+=(--label "$LABEL_FILTER")
  fi

  local raw err_file
  err_file="$(mktemp /tmp/gh-issue-list-err.XXXXXX)"
  if ! raw="$(gh "${gh_args[@]}" 2>"$err_file")"; then
    log "WARNING: failed to list GitHub issues; will retry next run. $(tr '\n' ' ' < "$err_file")"
    rm -f "$err_file"
    TRANSIENT_ERRORS=1
    return
  fi
  rm -f "$err_file"

  local count
  count="$(printf '%s' "$raw" | jq 'length')"
  log "Found $count open GitHub issue(s)."
  if [ "$count" -eq 0 ]; then
    return
  fi

  local import_file
  import_file="$(mktemp /tmp/gh-issues-import.XXXXXX.jsonl)"
  printf '' > "$import_file"
  DEPENDENCY_FILE="$(mktemp /tmp/gh-issues-deps.XXXXXX)"
  printf '' > "$DEPENDENCY_FILE"
  while IFS= read -r row; do
    local number title body url updated_at labels id ref
    number="$(printf '%s' "$row" | jq -r '.number')"
    title="$(printf '%s' "$row" | jq -r '.title')"
    body="$(printf '%s' "$row" | jq -r '.body // ""')"
    url="$(printf '%s' "$row" | jq -r '.url')"
    updated_at="$(printf '%s' "$row" | jq -r '.updatedAt')"
    labels="$(printf '%s' "$row" | jq -r '[.labels[].name] | join(",")')"
    id="${BEAD_PREFIX:+${BEAD_PREFIX}-}gh-$number"
    # Declarations are collected for every issue, settled bead or not: an
    # edge is a statement about order, and adding one does not rewrite the
    # status that `is_settled_bead` protects.
    while IFS= read -r ref; do
      [ -z "$ref" ] && continue
      printf '%s %s\n' "$id" "${BEAD_PREFIX:+${BEAD_PREFIX}-}gh-$ref" >> "$DEPENDENCY_FILE"
    done <<< "$(issue_dependency_refs "$body")"
    if is_settled_bead "$id"; then
      log "Skipping $id (issue #$number): the bead is not open, and intake does not reopen it."
      continue
    fi
    github_import_record "$id" "$title" "$body" "$number" "$url" "$labels" "$updated_at" >> "$import_file"
  done <<< "$(printf '%s' "$raw" | jq -c '.[]')"

  run_bd_import "$import_file" "GitHub issues"
  rm -f "$import_file"
}

# The issue numbers one issue declares ahead of itself, in declaration order.
#
# STRUCTURE, NOT PROSE (hy-9cf). These bodies discuss other issues constantly
# -- "#25 remains the authoritative benchmark" is a comparison, not a gate --
# so only what a `## Depends on` heading covers is read as an edge. That
# heading is already how every gated issue in this repository is written, which
# is why no phrase list ("do not begin before", "after #N is green") is
# matched: a phrase list is a guess about English, and a heading is a
# declaration. An entry under it that names no issue ("Contract checks begin
# now", "Existing tests/compose infrastructure") declares no edge and is
# ignored rather than warned about; a chain written on one line
# (`- #27 → #17 → #43`) is every issue in it, because that is what the author
# of #34 meant by putting all of them ahead of it.
issue_dependency_refs() {
  printf '%s\n' "$1" \
    | awk '
        /^[[:space:]]*##+[[:space:]]*[Dd]epends[[:space:]]+on/ { inside=1; next }
        /^[[:space:]]*##+[[:space:]]/ { inside=0 }
        inside { print }
      ' \
    | grep -oE '#[0-9]+' \
    | tr -d '#' \
    | awk '!seen[$0]++'
}

# Adds every declared edge that both ends exist for. Additive only, and never
# removes one: this script cannot tell an edge it added last week from one the
# Mayor added by hand (hy-gh-32 -> hy-gh-34 was exactly that), so reconciling
# by deletion would silently destroy human decisions to fix a machine's
# bookkeeping.
#
# `bd dep add` was measured for the four cases that matter: re-adding an
# existing edge succeeds and does not duplicate it, an unknown id fails, a
# cycle is refused by bd itself, and a self-dependency is refused. None of
# those may fail the pass -- a mis-declared chain in one issue body must not
# stop intake for the rest -- so each is logged and the run continues.
apply_declared_dependencies() {
  if [ -z "${DEPENDENCY_FILE:-}" ] || [ ! -s "$DEPENDENCY_FILE" ]; then
    return
  fi
  if [ -z "$RIG_PATH" ] || [ ! -d "$RIG_PATH" ]; then
    return
  fi
  # Read the ids again rather than reusing the pre-import set: a bead created
  # by this very pass is a legitimate end of an edge.
  local present id blocker out
  present="$(cd "$RIG_PATH" && bd list --all --limit 0 --json 2>/dev/null | jq -r '.[].id' 2>/dev/null)"
  while read -r id blocker; do
    [ -z "$id" ] && continue
    if ! id_is_listed "$id" "$present" || ! id_is_listed "$blocker" "$present"; then
      log "Skipping declared dependency $id -> $blocker: no bead for one end of it."
      continue
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
      log "DRY-RUN: would declare $id depends on $blocker."
      continue
    fi
    if out="$(cd "$RIG_PATH" && bd dep add "$id" "$blocker" 2>&1)"; then
      log "Declared $id depends on $blocker."
    else
      log "WARNING: could not declare $id depends on $blocker. $(printf '%s' "$out" | tr '\n' ' ')"
    fi
  done < "$DEPENDENCY_FILE"
}

# Return merged commits carrying an exact completion trailer. A passing mention
# of another bead is dependency context, not completion proof.
completion_commit_shas() {
  local bead_id="$1"
  local ref="${2:-origin/main}"
  git log "$ref" --extended-regexp --grep="^Completes-Bead: ${bead_id}$" --format='%H' 2>/dev/null
}

# Closes+comments the GitHub side of a closed bead only after a merged commit
# carries `Completes-Bead: <id>`. Bead state or a free-text commit mention is
# insufficient.
#
# Idempotent by construction, not by tracked state: it checks the GitHub
# issue's live state first and only acts if it's still open there.
push_completed_github_issues() {
  if [ -z "$GH_REPO" ]; then
    return
  fi
  if [ -z "$RIG_PATH" ] || [ ! -d "$RIG_PATH" ]; then
    return
  fi

  local closed err_file
  err_file="$(mktemp /tmp/bd-list-closed-err.XXXXXX)"
  if ! closed="$(cd "$RIG_PATH" && bd list --status closed --json 2>"$err_file")"; then
    log "WARNING: failed to query closed beads for GitHub push-back. $(tr '\n' ' ' < "$err_file")"
    rm -f "$err_file"
    TRANSIENT_ERRORS=1
    return
  fi
  rm -f "$err_file"

  local rows
  rows="$(printf '%s' "$closed" | jq -c --arg prefix "https://github.com/$GH_REPO/issues/" '.[] | select((.external_ref // "") | startswith($prefix))')"
  [ -z "$rows" ] && return

  git fetch origin main >/dev/null 2>&1

  while IFS= read -r row; do
    [ -z "$row" ] && continue
    local bead_id number gh_state
    bead_id="$(printf '%s' "$row" | jq -r '.id')"
    number="$(printf '%s' "$row" | jq -r '.external_ref | split("/") | last')"

    gh_state="$(gh issue view "$number" --repo "$GH_REPO" --json state -q .state 2>/dev/null)"
    if [ "$gh_state" != "OPEN" ]; then
      continue
    fi

    local commit_shas
    commit_shas="$(completion_commit_shas "$bead_id")"
    if [ -z "$commit_shas" ]; then
      log "INFO: $bead_id is closed in beads but no merged commit has its exact completion trailer; leaving GitHub issue #$number open."
      continue
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      log "DRY-RUN: would close GitHub issue #$number ($bead_id) with its commit summary."
      continue
    fi

    local comment_body sha
    comment_body="Completed by Gas Town ($bead_id):"
    while IFS= read -r sha; do
      [ -z "$sha" ] && continue
      comment_body="$comment_body

---
$(git log -1 --format='**%s**%n%n%b' "$sha")"
    done <<< "$commit_shas"

    if gh issue comment "$number" --repo "$GH_REPO" --body "$comment_body" >/dev/null 2>&1 \
       && gh issue close "$number" --repo "$GH_REPO" >/dev/null 2>&1; then
      log "Closed GitHub issue #$number with commit summary ($bead_id)."
    else
      log "WARNING: failed to close/comment GitHub issue #$number for $bead_id."
      TRANSIENT_ERRORS=1
    fi
  done <<< "$rows"
}

import_markdown_inbox() {
  log "Scanning markdown inbox in $INBOX_DIR..."
  local files
  if ! files=$(find "$INBOX_DIR" -type f -name '*.md' 2>/dev/null); then
    log "WARNING: failed to list markdown inbox files."
    TRANSIENT_ERRORS=1
    return
  fi

  local import_file
  import_file="$(mktemp /tmp/md-issues-import.XXXXXX.jsonl)"
  printf '' > "$import_file"
  local seen_ids=()
  local count=0

  while IFS= read -r file; do
    [ -z "$file" ] && continue
    local frontend
    if ! frontend="$(parse_markdown_frontmatter "$file" 2>/dev/null)"; then
      log "Skipping markdown file without valid frontmatter: $file"
      continue
    fi

    local title type priority description
    title="$(printf '%s' "$frontend" | sed -n '1p')"
    type="$(printf '%s' "$frontend" | sed -n '2p' | tr '[:upper:]' '[:lower:]')"
    priority="$(printf '%s' "$frontend" | sed -n '3p')"
    if [ -z "$priority" ] || ! [[ "$priority" =~ ^[0-9]+$ ]]; then
      priority="2"
    fi
    if [ "$type" != "task" ] && [ "$type" != "bug" ] && [ "$type" != "research" ]; then
      type="task"
    fi
    description="$(markdown_description "$file" 2>/dev/null)"

    # Bead ID is derived from the filename, not content, so edits to an
    # existing file keep updating the same bead. Collisions (two files
    # slugifying to the same name) are only possible within a single run,
    # so detecting them against an in-memory list here is enough — no
    # persisted state needed.
    local slug prefix id existing
    slug="$(sanitize_slug "$(basename "$file" .md)")"
    prefix="${BEAD_PREFIX:+${BEAD_PREFIX}-}md"
    id="$prefix-$slug"
    for existing in "${seen_ids[@]:-}"; do
      if [ "$existing" = "$id" ]; then
        id="$prefix-${slug}-$(hash_string "$file" | cut -c1-8)"
        break
      fi
    done
    seen_ids+=("$id")

    if is_settled_bead "$id"; then
      log "Skipping $id ($file): the bead is not open, and intake does not reopen it."
      continue
    fi

    local updated_at
    updated_at="$(date -u -r "$file" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
    [ -z "$updated_at" ] && updated_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

    # bd has no native "research" issue type; the closest built-in match is
    # "spike" (timeboxed investigation), so map it at import time while the
    # frontmatter and docs keep the friendlier "research" name.
    local bd_type="$type"
    if [ "$bd_type" = "research" ]; then
      bd_type="spike"
    fi
    markdown_import_record "$id" "$title" "$description" "$bd_type" "$priority" "$file" "$updated_at" >> "$import_file"
    count=$((count + 1))
  done <<< "$files"

  if [ "$count" -eq 0 ]; then
    rm -f "$import_file"
    log "No valid markdown files found in inbox."
    return
  fi

  run_bd_import "$import_file" "markdown inbox"
  rm -f "$import_file"
}

run_once() {
  NEW_BEAD_IDS=""
  resolve_bead_prefix
  fetch_bead_states
  import_github_issues
  import_markdown_inbox
  if [ "$DRY_RUN" -eq 0 ]; then
    commit_pending_changes
  fi
  apply_declared_dependencies
  rm -f "$DEPENDENCY_FILE"
  push_completed_github_issues
  if [ "$DRY_RUN" -eq 0 ]; then
    notify_mayor
  fi
  if [ "$TRANSIENT_ERRORS" -gt 0 ]; then
    log "Completed with transient errors; retry on next scheduled run."
    return 1
  else
    log "Completed successfully."
    return 0
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  parse_args "$@"
  load_yaml_config
  config_defaults
  prepare_environment

  if [ "$LOOP_MODE" -eq 1 ]; then
    log "Starting loop mode (interval=$INTERVAL seconds)."
    while true; do
      run_once
      sleep "$INTERVAL"
    done
  else
    run_once
  fi
fi
