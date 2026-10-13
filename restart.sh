#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/.bot.pid"

# Убиваем по PID-файлу
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    kill -9 "$OLD_PID" 2>/dev/null
    rm -f "$PIDFILE"
fi

# Убиваем любые оставшиеся bot.py процессы
ps aux | grep "python.*bot.py" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
sleep 2

# Запускаем один
cd "$DIR"
source .venv/bin/activate
python3 bot.py >> /tmp/insta-bot.log 2>&1 &
echo $! > "$PIDFILE"
echo "Запущен PID: $(cat $PIDFILE)"
sleep 2
echo "Процессов bot.py: $(ps aux | grep 'python.*bot.py' | grep -v grep | wc -l | tr -d ' ')"
