# The Dovecot and Mailpit images for the dev and test mail backends, sourced by scripts/dev-mail.sh
# and scripts/test-mail.sh; CI's backend-tests.yaml uses the same pins. Copied from mailroom-mcp's
# scripts/images.sh; Renovate tracks these pins (regex in renovate.json).
# shellcheck disable=SC2034
dovecot_image="docker.io/dovecot/dovecot:2.4.5@sha256:c807be4fb5a97d9c3a90770569d3a6c4cbdcb36742ad41f90409cbd929166553"
mailpit_image="docker.io/axllent/mailpit:v1.31.3@sha256:ed9b00c609e77e99c79b93f1178255ebc271868920f2c69a8d166bd5634ed10d"
