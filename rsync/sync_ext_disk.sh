#!/usr/bin/env bash

set -e

SRC=/mnt/src
DEST=/mnt/dst
DIRS=(documents-crypt scans-crypt photos-crypt media-crypt games-crypt)

if mount | grep -s "${SRC}" > /dev/null; then
        for dir in "${DIRS[@]}"; do
                if [ -d "${SRC}/${dir}" ]; then
                        rsync --progress -vahb --backup-dir="${DEST}/deleted_files/${dir}" --delete --exclude '.stversions/' --ignore-existing "${SRC}/${dir}/" "${DEST}/${dir}/"
                fi
        done
fi

echo "Success at $(date)" > "${DEST}/backups.log"
date +%s > "${SRC}/.last-successful-backup"

sudo umount "${DEST}"
sudo umount "${SRC}"
