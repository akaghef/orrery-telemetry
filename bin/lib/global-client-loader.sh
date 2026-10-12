#!/bin/bash
# One loader for partial installations. Normal selection belongs to Python.
if [[ -n "${BASH_SOURCE:-}" ]]; then _ags_client_loader_src="${BASH_SOURCE[0]}"; else _ags_client_loader_src="$0"; fi
AGS_CLIENT_LOADER_DIR="$(cd "$(dirname "$_ags_client_loader_src")" && pwd)"

ags_client_missing_admission() {
  local argument context
  context="$AGS_CLIENT_LOADER_DIR/../../runtime-client.json"
  for argument in "$@"; do
    case "$argument" in --context|--context=*|--runtime-mode|--runtime-mode=*|--server-config|--server-config=*) printf '%s\n' GLOBAL_ENTRY_UNAVAILABLE >&2; return 2 ;; esac
  done
  if [[ "${AGENTSTACK_RUNTIME_MODE-legacy}" != legacy || -n "${AGENTSTACK_GLOBAL_SERVER_CONFIG:-}" || -n "${AGENTSTACK_CLIENT_CONFIG:-}" || -e "$context" || -L "$context" ]]; then
    printf '%s\n' GLOBAL_ENTRY_UNAVAILABLE >&2
    return 2
  fi
  return 125
}

ags_client_select() {
  local response ags_selection_rc
  ags_client_missing_admission "$@" 2>/dev/null && ags_selection_rc=0 || ags_selection_rc=$?
  [[ "$ags_selection_rc" != 125 ]] || return 125
  if [[ ! -f "$AGS_CLIENT_LOADER_DIR/runtime_client.py" || ! -f "$AGS_CLIENT_LOADER_DIR/agentstack-register.sh" ]]; then
    ags_client_missing_admission "$@"; return $?
  fi
  response="$(python3 "$AGS_CLIENT_LOADER_DIR/runtime_client.py" select "$@" </dev/null)" && ags_selection_rc=0 || ags_selection_rc=$?
  if [[ "$ags_selection_rc" == 64 ]]; then return 2; fi
  if [[ "$ags_selection_rc" != 0 ]]; then
    ags_client_missing_admission "$@"; return $?
  fi
  case "$response" in
    orrery-client-global-v1|"orrery-client-global-v1"$'\n'*)
      export AGENTSTACK_RUNTIME_MODE=global
      if [[ "$response" == *$'\n'* ]]; then
        export AGENTSTACK_CLIENT_CONFIG="${response#*$'\n'}"
      else
        unset AGENTSTACK_CLIENT_CONFIG
      fi
      return 0 ;;
    orrery-client-legacy-v1) return 125 ;;
    *) ags_client_missing_admission "$@" ;;
  esac
}

ags_client_bootstrap() {
  local env_file="$1" inherited_mode="${AGENTSTACK_RUNTIME_MODE-}" mode_set="${AGENTSTACK_RUNTIME_MODE+set}" choice admission_rc
  local inherited_config="${AGENTSTACK_GLOBAL_SERVER_CONFIG-}" config_set="${AGENTSTACK_GLOBAL_SERVER_CONFIG+set}"
  local env_rc=0 errexit_set= setting assignment
  [[ "$-" != *e* ]] || errexit_set=set
  local inherited_context="${AGENTSTACK_CLIENT_CONFIG-}" context_set="${AGENTSTACK_CLIENT_CONFIG+set}"
  if [[ -e "$env_file" || -L "$env_file" ]]; then
    [[ -f "$env_file" && -r "$env_file" ]] || { printf '%s\n' RUNTIME_DEFAULT_UNAVAILABLE >&2; return 2; }
    set +e
    if [[ "${2:-}" == --literal ]]; then
      # The installer already resolves settings as data. Reuse that reader;
      # never execute unrelated assignments or shell expansions during install.
      if ! declare -F agentstack_installed_env_value >/dev/null; then
        env_rc=2
      else
        for setting in AGENTSTACK_RUNTIME_MODE AGENTSTACK_GLOBAL_SERVER_CONFIG AGENTSTACK_CLIENT_CONFIG; do
          assignment="$(agentstack_installed_env_value "$setting" "$env_file" --shell)" || { env_rc=2; break; }
          eval "$assignment" || { env_rc=2; break; }
        done
      fi
    elif declare -F agentstack_load_installed_env >/dev/null; then
      agentstack_load_installed_env "$env_file" || env_rc=$?
    else
      . "$env_file" || env_rc=$?
    fi
    [[ "$errexit_set" != set ]] || set -e
    if [[ "$env_rc" != 0 ]]; then
      printf '%s\n' RUNTIME_DEFAULT_UNAVAILABLE >&2
      return 2
    fi
  fi
  if [[ "$mode_set" == set ]]; then export AGENTSTACK_RUNTIME_MODE="$inherited_mode"; fi
  if [[ "$config_set" == set ]]; then export AGENTSTACK_GLOBAL_SERVER_CONFIG="$inherited_config";
  elif [[ "$mode_set" == set ]]; then unset AGENTSTACK_GLOBAL_SERVER_CONFIG; fi
  if [[ "$context_set" == set ]]; then export AGENTSTACK_CLIENT_CONFIG="$inherited_context";
  elif [[ "$mode_set" == set ]]; then unset AGENTSTACK_CLIENT_CONFIG; fi
  ags_client_missing_admission 2>/dev/null && admission_rc=0 || admission_rc=$?
  if [[ "$admission_rc" == 125 ]]; then export AGENTSTACK_RUNTIME_MODE=legacy; return 0; fi
  if [[ ! -f "$AGS_CLIENT_LOADER_DIR/runtime_client.py" ]]; then
    ags_client_missing_admission || { [[ "$?" == 125 ]] && return 0; return 2; }
  fi
  if [[ "$mode_set" != set ]]; then
    choice="$(python3 "$AGS_CLIENT_LOADER_DIR/runtime_client.py" select --installed-default --shell)" || return 2
  else
    choice="$(python3 "$AGS_CLIENT_LOADER_DIR/runtime_client.py" select --shell)" || return 2
  fi
  eval "$choice"
}

ags_client_entry() {
  local entry="$1" ags_selection_rc
  shift
  ags_client_select "$@" && ags_selection_rc=0 || ags_selection_rc=$?
  [[ "$ags_selection_rc" == 0 ]] || return "$ags_selection_rc"
  case "$entry" in
    pre-edit|post-edit|end-session|session-start|registered|record-self)
      [[ "${AGENTSTACK_MAIL_DISABLED:-}" != 1 ]] || return 0 ;;
  esac
  . "$AGS_CLIENT_LOADER_DIR/agentstack-register.sh"
  if ! declare -F ags_global_entry >/dev/null; then
    ags_client_missing_admission "$@"; return $?
  fi
  ags_global_entry "$entry" "$@" && ags_selection_rc=0 || ags_selection_rc=$?
  if [[ "$ags_selection_rc" == 125 ]]; then
    ags_client_missing_admission "$@"; return $?
  fi
  return "$ags_selection_rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ "${1:-}" == select ]]; then shift; ags_client_select "$@"; else ags_client_entry "$@"; fi
fi
