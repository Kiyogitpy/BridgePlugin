#!/usr/bin/env bash
#
# Re-sync the vendored m8aq api readers from upstream.
#
# The vendored tree at src/main/java/com/example/m8aq/api/ is an upstream copy, REPACKAGED
# (see VENDORED.md): every file's package and import lines were rewritten from
# net.runelite.client.plugins.m8aq to com.example.m8aq on 2026-09-20, so it is no longer
# byte-identical to upstream. It is otherwise verbatim, and that is what keeps this a clean
# overwrite plus one rename pass rather than a conflict-ridden rewrite of 71 files -- see that
# directory's VENDORED.md for the rename step this script's output still has to go through.
#
# New upstream reader classes become new /api/... endpoints with no code change on our side.
# An upstream FIELD RENAME silently changes JSON keys the Python side may depend on, which is
# why this prints a diffstat and refuses to commit anything itself.
#
# Usage:  bash tools/sync-m8aq.sh [ref]
#           ref  branch, tag or commit to sync to (default: main)

set -euo pipefail

UPSTREAM="https://github.com/RLitePlus/m8aq-plugins"
SUBTREE="src/main/java/com/example/m8aq/api"
REF="${1:-main}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
DEST="$REPO_ROOT/$SUBTREE"
STAMP="$REPO_ROOT/src/main/java/com/example/m8aq/VENDORED.md"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Cloning $UPSTREAM @ $REF ..."
git clone --quiet --depth 1 --branch "$REF" "$UPSTREAM" "$TMP/upstream" 2>/dev/null \
  || git clone --quiet "$UPSTREAM" "$TMP/upstream"
git -C "$TMP/upstream" checkout --quiet "$REF"

SRC="$TMP/upstream/$SUBTREE"
if [ ! -d "$SRC" ]; then
	echo "ERROR: upstream has no $SUBTREE -- the layout moved. Sync by hand and update this script." >&2
	exit 1
fi

SHA="$(git -C "$TMP/upstream" rev-parse HEAD)"
SUBJECT="$(git -C "$TMP/upstream" log -1 --pretty=%s)"
DATE="$(git -C "$TMP/upstream" log -1 --pretty=%cd --date=short)"

# A clean overwrite: files deleted upstream must disappear here too, or ReaderCatalog keeps
# discovering a reader that no longer exists upstream.
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -r "$SRC" "$DEST"

COUNT="$(find "$DEST" -name '*.java' | wc -l | tr -d ' ')"
READERS="$(grep -rl 'public static .* getState(Client client)' "$DEST" | wc -l | tr -d ' ')"

python - "$STAMP" "$SHA" "$SUBJECT" "$DATE" "$REF" "$COUNT" <<'PY'
import datetime, re, sys
path, sha, subject, date, ref, count = sys.argv[1:7]
text = open(path, encoding="utf-8").read()
subs = {
	r"(\*\*Branch:\*\* ).*":            r"\g<1>" + ref,
	r"(\*\*Synced commit:\*\* ).*":     r'\g<1>%s ("%s", %s)' % (sha, subject, date),
	r"(\*\*Synced on:\*\* ).*":         r"\g<1>" + datetime.date.today().isoformat(),
	r"(\*\*Subtree taken:\*\* .*?\().*?(\))": r"\g<1>%s files\g<2>" % count,
}
for pattern, repl in subs.items():
	text = re.sub(pattern, repl, text, count=1)
open(path, "w", encoding="utf-8", newline="\n").write(text)
PY

echo
echo "Synced $COUNT files ($READERS readers) at $SHA"
echo
git -C "$REPO_ROOT" --no-pager diff --stat -- "$SUBTREE" "$STAMP" || true
echo
echo "Next: ./gradlew.bat build, then GET /api/index and diff the endpoint list."
echo "Nothing was committed."
