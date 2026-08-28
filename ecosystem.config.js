module.exports = {
  apps: [
    {
      name: "cfa-pagermon-bridge",
      script: ".venv/Scripts/python.exe",
      args: "-m src.cfa_pagermon_bridge.main",
      cwd: __dirname,
      interpreter: "none",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "200M",
      env: {
        NODE_ENV: "production",
      },
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "logs/bridge-error.log",
      out_file: "logs/bridge-out.log",
      merge_logs: true,
    },
  ],
};
