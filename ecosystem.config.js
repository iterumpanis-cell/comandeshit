module.exports = {
  apps: [
    {
      name: "hitsystems-bot",
      script: "C:\\Users\\usuari\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe",
      args: "bot.py",
      cwd: "C:\\botTel",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
