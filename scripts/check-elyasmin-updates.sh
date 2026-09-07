#!/bin/bash
set -e

REPO_DIR="/opt/elyasmin"
ENV_FILE="/opt/elyasmin/scripts/.alerts.env"
STATE_FILE="/opt/elyasmin/scripts/.last_known_main"

source "$ENV_FILE"

cd "$REPO_DIR"

# آخر main معروف قبل الفحص ده
BEFORE=$(git rev-parse main)

# جيب أي حاجة جديدة من GitHub
git fetch origin --prune --quiet

AFTER=$(git rev-parse origin/main)

# لو مفيش فرق، اخرج بصمت — مفيش إيميل
if [ "$BEFORE" == "$AFTER" ]; then
    exit 0
fi

# فيه فرق — اجمع التفاصيل
COMMIT_COUNT=$(git log "$BEFORE".."$AFTER" --oneline | wc -l)
COMMIT_LIST=$(git log "$BEFORE".."$AFTER" --oneline)
MIGRATION_FILES=$(git diff --stat "$BEFORE".."$AFTER" -- migrations/ || echo "لا يوجد")
REQUIREMENTS_CHANGED=$(git diff --stat "$BEFORE".."$AFTER" -- requirements.txt || echo "لا يوجد")
FILES_CHANGED=$(git diff --stat "$BEFORE".."$AFTER" | tail -3)

# ابني نص الرسالة
BODY=$(cat << MSG_EOF
فيه تحديثات جديدة على main في مشروع المزرعة (Elyasmin).

عدد الـ commits: $COMMIT_COUNT

--- قائمة الـ commits ---
$COMMIT_LIST

--- تغييرات في migrations/ ---
$MIGRATION_FILES

--- تغييرات في requirements.txt ---
$REQUIREMENTS_CHANGED

--- إجمالي الملفات المتأثرة ---
$FILES_CHANGED

--------
هذا كشف فقط — لم يتم تنفيذ أي pull أو تعديل على السيرفر أو القاعدة.
راجع مع Claude قبل أي deploy.
MSG_EOF
)

# ابعت الإيميل عن طريق Resend
curl -s -X POST 'https://api.resend.com/emails' \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json, sys
body = '''$BODY'''
print(json.dumps({
    'from': '$ALERT_FROM',
    'to': '$ALERT_TO',
    'subject': '[Elyasmin] تحديثات جديدة على main ($COMMIT_COUNT commit)',
    'text': body
}))
")" > /dev/null

echo "Alert sent — $COMMIT_COUNT new commit(s) detected."
