#!/bin/bash

# Change to the project directory
cd /home/praneeth7781/f1nsight-api-2

# Pull the latest changes from GitHub
git pull origin master

# Run the update script
cd scripts
python3 update.py
cd ..
# Check if there are changes to commit
if [[ `git status --porcelain` ]]; then
  # Changes exist, so commit and push
  git add .
  git commit -m "Daily update $(date +%Y-%m-%d)"
  git push origin main
  echo "Changes pushed to GitHub"
else
  # No changes
  echo "No changes to commit"
fi