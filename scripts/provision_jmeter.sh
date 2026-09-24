#!/usr/bin/env bash
# Provision Apache JMeter 5.6.3 with SHA-512 checksum verification.
# Usage: scripts/provision_jmeter.sh [dest_dir]
set -euo pipefail

VERSION="5.6.3"
DEST="${1:-.jmeter}"
TARBALL="apache-jmeter-${VERSION}.tgz"
# Official Apache archive (dlcdn is the live mirror; archive.apache.org is the fallback).
URL="https://archive.apache.org/dist/jmeter/binaries/${TARBALL}"
SHA_URL="${URL}.sha512"

mkdir -p "${DEST}"
cd "${DEST}"

echo "Downloading ${TARBALL} ..."
curl -fSL "${URL}" -o "${TARBALL}"
curl -fSL "${SHA_URL}" -o "${TARBALL}.sha512"

echo "Verifying checksum ..."
# The .sha512 file contains the hash (and sometimes the filename); normalise it.
EXPECTED="$(awk '{print $1}' "${TARBALL}.sha512" | tr -d '\n' | tr 'A-F' 'a-f')"
ACTUAL="$(sha512sum "${TARBALL}" | awk '{print $1}')"
if [ "${EXPECTED}" != "${ACTUAL}" ]; then
  echo "CHECKSUM MISMATCH" >&2
  echo "expected: ${EXPECTED}" >&2
  echo "actual:   ${ACTUAL}" >&2
  exit 1
fi
echo "Checksum OK."

tar xzf "${TARBALL}"
echo "JMeter available at: $(pwd)/apache-jmeter-${VERSION}/bin/jmeter"
