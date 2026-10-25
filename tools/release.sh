#!/usr/bin/env bash
# Publishes a Kiln release that carries YOUR signature. Kiln installs updates only when this signature checks out,
# so nobody else (not a stolen GitHub account, not a leaked token) can push an update, even to a public repo.
#
#   tools/release.sh 1.2.1
#
# Needs: a release key (see docs/RELEASING.md), `gh` logged in, a clean checkout of main that is pushed.
# It asks for the key's passphrase once, when it signs.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?usage: tools/release.sh X.Y.Z}"
KEY="${KILN_RELEASE_KEY:-$HOME/.ssh/kiln-release}"
TAG="v$VERSION"
DRY="${KILN_RELEASE_DRY:-0}"          # 1: build, sign and verify, but touch nothing on GitHub (for trying it out)
die() { echo "release: $*" >&2; exit 1; }
need() { [ "$DRY" = 1 ] && { echo "  (dry run) skipping: $*"; return 0; }; die "$*"; }
say() { printf '\033[38;2;217;119;87m==>\033[0m %s\n' "$*"; }

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 1.2.3"
[ -f "$KEY" ] && [ -f "$KEY.pub" ] || die "no release key at $KEY (docs/RELEASING.md shows how to make one)"
[ -f share/release-key.pub ] || die "share/release-key.pub is missing: Kiln would not trust this release"
grep -qF "$(cut -d' ' -f1,2 "$KEY.pub")" share/release-key.pub || die "your key is not listed in share/release-key.pub"
[ -z "$(git status --porcelain)" ] || need "the working tree has uncommitted changes"
[ "$(git branch --show-current)" = main ] || need "release from the main branch"
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || need "main is not the same as origin/main (push or pull first)"
[ "$(cat share/VERSION)" = "$VERSION" ] || need "share/VERSION says $(cat share/VERSION), not $VERSION"
grep -q "^## $VERSION" CHANGELOG.md || need "CHANGELOG.md has no '## $VERSION' section"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && need "tag $TAG already exists"

say "Running the tests"
/usr/bin/python3 -m unittest discover -s tests >/dev/null 2>&1 || die "the tests fail (run: python3 -m unittest discover -s tests)"

say "Building the archive"
mkdir -p dist
ARCHIVE="dist/kiln-$VERSION.tar.gz"
rm -f "$ARCHIVE" "$ARCHIVE.sig"
git archive --format=tar.gz --prefix="kiln-$VERSION/" -o "$ARCHIVE" HEAD

say "Signing it (your key's passphrase is asked here)"
ssh-keygen -Y sign -f "$KEY" -n kiln-release "$ARCHIVE"

say "Checking the signature the way Kiln will"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
sed 's/^/kiln-release /' share/release-key.pub | grep -v '^kiln-release #' > "$TMP/allowed"
ssh-keygen -Y verify -f "$TMP/allowed" -I kiln-release -n kiln-release -s "$ARCHIVE.sig" < "$ARCHIVE" >/dev/null \
  || die "the signature does not verify against share/release-key.pub"

NOTES="$TMP/notes.md"
awk -v v="## $VERSION" 'index($0, v) == 1 {on=1; next} /^## / {on=0} on' CHANGELOG.md > "$NOTES"
printf '\n## Install or update\n\nNew install: `git clone`, then `./install.sh`. Already running Kiln: `kiln update`, or Settings → General → Kiln updates.\nUpdates are installed only when they carry the maintainer'"'"'s signature.\n' >> "$NOTES"

[ "$DRY" = 1 ] && { say "Dry run: signed and verified $ARCHIVE; nothing was tagged or published."; exit 0; }
say "Tagging $TAG and publishing"
git tag -a "$TAG" -m "Kiln $VERSION"
git push -q origin "$TAG"
gh release create "$TAG" "$ARCHIVE" "$ARCHIVE.sig" --title "Kiln $VERSION" --notes-file "$NOTES" --verify-tag --latest
say "Done: $TAG is out and signed."
