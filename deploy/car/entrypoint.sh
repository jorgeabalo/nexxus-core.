#!/bin/sh
set -eu
# A persistent mount replaces the image directory's ownership.
# Initialize only this dedicated data directory, then drop privileges.
chown aita:aita /data
chmod 700 /data
exec gosu aita "$@"
