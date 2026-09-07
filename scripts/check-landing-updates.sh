#!/bin/bash
set -e

REPO_DIR="/opt/elyasmin"
ENV_FILE="/opt/elyasmin/scripts/.alerts.env"

source "$ENV_FILE"
cd "$REPO_DIR"

BEFORE=$(git rev-parse HEAD)

git fetch origin --quiet

AFTER=$(git rev-parse origin/main)

if [ "$BEFORE" == "$AFTER" ]; then
    exit 0   # مفيش جديد خالص
fi

# فيه تغييرات جديدة — هل فيها حاجة في ملفات اللاندينج؟
LANDING_CHANGED=$(git diff --name-only "$BEFORE" "$AFTER" -- app/templates/landing app/static/landing app/blueprints/landing)

if [ -z "$LANDING_CHANGED" ]; then
    exit 0   # فيه تحديثات، بس مش في اللاندينج — سيبها لسكريبت check-elyasmin-updates
fi

echo "فيه تعديل جديد في اللاندينج — بدء الـ pull والـ deploy"

git pull --rebase origin main

systemctl restart elyasmin
sleep 3
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5004/)

STATUS_TEXT="نجح ✅"
if [ "$HTTP_CODE" != "200" ]; then
    STATUS_TEXT="تحذير ⚠️ — HTTP $HTTP_CODE بعد الـ restart"
fi

curl -s -X POST 'https://api.resend.com/emails' \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
print(json.dumps({
    'from': '$ALERT_FROM',
    'to': '$ALERT_TO',
    'subject': '[Elyasmin] تم تحديث اللاندينج تلقائياً',
    'text': '''تم رفع تعديل جديد في اللاندينج بيج تلقائياً.

الملفات: $LANDING_CHANGED
الحالة: $STATUS_TEXT
'''
}))
")" > /dev/null

echo "Done."
