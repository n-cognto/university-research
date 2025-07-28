#!/bin/bash


# Rewrite Git commit history
git filter-branch --env-filter '
OLD_EMAIL="benardopeter4.com"
CORRECT_NAME="n-cognto"
CORRECT_EMAIL="benardopeter4@gmail.com"

if [ "$GIT_AUTHOR_EMAIL" = "$OLD_EMAIL" ]; then
    export GIT_AUTHOR_NAME="$CORRECT_NAME"
    export GIT_AUTHOR_EMAIL="$CORRECT_EMAIL"
fi

if [ "$GIT_COMMITTER_EMAIL" = "$OLD_EMAIL" ]; then
    export GIT_COMMITTER_NAME="$CORRECT_NAME"
    export GIT_COMMITTER_EMAIL="$CORRECT_EMAIL"
fi
' --tag-name-filter cat -- --branches --tags

# Clean up old references and optimize the repo
rm -rf .git/refs/original/
git reflog expire --expire=now --all
git gc --prune=now --aggressive

echo "✅ Email correction completed for Sustainable_Fishing repo."
echo "🚀 Now push changes to GitHub with:"
echo "   git push --force --all && git push --force --tags"
