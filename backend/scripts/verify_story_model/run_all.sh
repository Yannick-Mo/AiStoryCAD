#!/usr/bin/env bash
# 顺序执行四套验证。用法：run_all.sh <副本库名>
# 写库的两套只允许打名字里带 test / copy / migtest / verify 的库。
set -uo pipefail

DB_NAME="${1:-}"
if [ -z "$DB_NAME" ]; then
  echo "用法: $0 <副本库名>   （例如 storyforge_verify）"
  exit 2
fi
case "$DB_NAME" in
  *test*|*copy*|*migtest*|*verify*) ;;
  *) echo "拒绝执行：'$DB_NAME' 看起来不是副本库。写库的两项只能打副本。"; exit 2 ;;
esac

cd "$(dirname "$0")/../../.." || exit 2
BASE=$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)
URL="${BASE%/*}/$DB_NAME"
DIR=/app/scripts/verify_story_model
FAILED=0

echo "副本库 = $DB_NAME"

for script in check_editor_sync check_content_and_setups check_spec_questions check_writer_chain check_framework_render check_scale; do
  echo "──────── $script ────────"
  out=$(docker compose run --rm --no-deps -e DATABASE_URL="$URL" \
        -v "$PWD/backend:/app" backend python "$DIR/$script.py" 2>&1)
  echo "$out" | grep -E '^(  )?(PASS|FAIL)|^FAILURES'
  if ! echo "$out" | grep -q '^FAILURES: 0'; then
    FAILED=$((FAILED + 1))
    echo "  >>> $script 未全通过"
  fi
done

echo "==============================================="
if [ "$FAILED" -eq 0 ]; then
  echo "全部通过（FAILURES: 0）"
else
  echo "$FAILED 个脚本未全通过"
fi
exit "$FAILED"
