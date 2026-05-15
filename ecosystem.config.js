module.exports = {
  apps: [
    {
      name: "hitsystems-bot",
      script: "C:\\Users\\Usuario\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe",
      args: "bot.py",
      cwd: "C:\\Users\\Usuario\\CLAUDE CODE\\hitsystems-bot",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "hitsystems-auto-envia-feiner",
      script: "C:\\Users\\Usuario\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe",
      args: "bot.py --mode auto_envia",
      cwd: "C:\\Users\\Usuario\\CLAUDE CODE\\hitsystems-bot",
      interpreter: "none",
      cron_restart: "0 18 * * 1-5",
      autorestart: false,
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "hitsystems-auto-envia-caps",
      script: "C:\\Users\\Usuario\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe",
      args: "bot.py --mode auto_envia",
      cwd: "C:\\Users\\Usuario\\CLAUDE CODE\\hitsystems-bot",
      interpreter: "none",
      cron_restart: "0 13 * * 6,0",
      autorestart: false,
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
