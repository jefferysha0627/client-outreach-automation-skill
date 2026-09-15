#!/bin/zsh
set -e

cd "$(dirname "$0")/.."

python3 client-outreach/send-drafts.py

echo
echo "確認 Gmail 草稿都已檢查完畢後，輸入 SEND 再按 Enter 送出本批草稿。"
echo "直接按 Enter 會取消，不會寄出。"
read "answer?確認送出？"

if [[ "$answer" == "SEND" ]]; then
  python3 client-outreach/send-drafts.py --confirm
else
  echo "已取消，沒有送出任何草稿。"
fi

echo
echo "可以關閉這個視窗。"
