Install and enable backend service on Oracle Linux/Ubuntu:

```bash
sudo cp deploy/systemd/kinetic-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kinetic-backend
sudo systemctl start kinetic-backend
sudo systemctl status kinetic-backend
```

View logs:

```bash
journalctl -u kinetic-backend -f
```

Notes:
- Update `WorkingDirectory` and `ExecStart` paths in the service file to match your server path.
- Keep `--workers 1` for this bot-style in-memory workflow unless you externalize shared state.
