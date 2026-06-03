#!/bin/sh
set -eu

export AMARI_ACTION=delete
exec /opt/katana/amari-adapter/apply-core-slice.sh
