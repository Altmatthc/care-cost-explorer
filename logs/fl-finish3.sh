#!/bin/bash
cd /c/Users/cordl/Documents/care-cost-explorer || exit 1
TOKEN=$(gh auth token)
# Wait for the core API budget to actually reset (remaining near limit).
while true; do
  R=$(curl -s -H "Authorization: Bearer $TOKEN" https://api.github.com/rate_limit | python -c "import json,sys;d=json.load(sys.stdin)['resources']['core'];print(d['remaining'])" 2>/dev/null)
  if [ "$R" = "5000" ]; then echo "budget fully reset"; break; fi
  sleep 120
done
.venv/Scripts/python.exe scripts/publish_release.py --region fl > logs/fl-release-final3.log 2>&1
echo "PUBLISH_EXIT=$?" >> logs/fl-release-final3.log
tail -5 logs/fl-release-final3.log
