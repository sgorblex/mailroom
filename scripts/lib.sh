# Helpers for scripts/dev-mail.sh (sourced, not run). Copied from mailroom-mcp.

# Waits until the TLS service named $1 on 127.0.0.1:$2 greets with a first line matching $3.
await_greeting() {
  local line
  for _ in $(seq 1 30); do
    # The probe's exit status depends on timing once the server answers; only the line counts.
    line=$(echo | timeout 2 openssl s_client -connect "127.0.0.1:$2" -quiet 2>/dev/null | head -1 || true)
    if grep -q "$3" <<<"$line"; then
      return 0
    fi
    sleep 1
  done
  echo "$1 did not greet on 127.0.0.1:$2 within 30 s" >&2
  exit 1
}
