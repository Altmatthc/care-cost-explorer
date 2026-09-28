#!/bin/bash
# Waits for the GitHub API budget to FULLY reset, then finishes FL catalogue.
cd /c/Users/cordl/Documents/care-cost-explorer || exit 1
TOKEN=$(gh auth token)
while true; do
  R=$(curl -s -H "Authorization: Bearer $TOKEN" https://api.github.com/rate_limit | python -c "import json,sys;print(json.load(sys.stdin)['resources']['core']['remaining'])" 2>/dev/null)
  if [ "$R" = "5000" ]; then echo "budget fully reset"; break; fi
  sleep 120
done
.venv/Scripts/python.exe scripts/publish_release.py --region fl > logs/fl-release-final3.log 2>&1
echo "PUBLISH_EXIT=$?" >> logs/fl-release-final3.log
tail -5 logs/fl-release-final3.log
