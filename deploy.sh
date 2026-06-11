#!/bin/bash
# Run on your Hetzner server to set up the bot from scratch.
# Usage: bash deploy.sh

set -e

BOT_DIR="/opt/hackamaps_bot"

echo "==> Creating bot directory..."
sudo mkdir -p "$BOT_DIR"
sudo chown "$USER":"$USER" "$BOT_DIR"

echo "==> Copying files..."
cp main.py requirements.txt "$BOT_DIR/"

echo "==> Creating virtual environment..."
cd "$BOT_DIR"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo ""
echo "==> NEXT STEP: Create your .env file:"
echo "    nano $BOT_DIR/.env"
echo ""
echo "    Paste your keys (copy from .env.example), then save."
echo ""
echo "==> After setting up .env, test the bot:"
echo "    cd $BOT_DIR && ./venv/bin/python main.py"
echo ""
echo "==> To add the cron job (runs every 2 hours):"
echo "    crontab -e"
echo ""
echo "    Then add this line:"
echo "    0 */2 * * * cd $BOT_DIR && ./venv/bin/python main.py >> bot.log 2>&1"
echo ""
echo "==> Done!"
