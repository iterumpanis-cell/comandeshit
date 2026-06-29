module.exports = {
  apps: [
    {
      name: "hitsystems-bot-proves",
      script: "C:\\Users\\Usuario\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe",
      args: "bot.py",
      cwd: "C:\\Users\\Usuario\\CLAUDE CODE\\comandesHitProbes",
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
      name: "ia-worker-proves",
      script: "C:\\Users\\Usuario\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe",
      args: "ia_worker.py",
      cwd: "C:\\Users\\Usuario\\CLAUDE CODE\\comandesHitProbes",
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
