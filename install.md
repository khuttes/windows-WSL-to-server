# HomeDash Windows installation guide

This guide installs the Python requirements and starts a local preview of HomeDash on Windows 10 or 11. It does not configure Linux services, WSL, networking, or a public-facing server.

## Requirements

- Windows 10 or Windows 11.
- Python 3.10 or later.
- The project folder: `D:\Projects\windows WSL to server`.
- Internet access for the first dependency installation.

Project setup does not require administrator privileges. Installing Python may request confirmation through Windows Package Manager.

## 1. Install Python if needed

Open **Windows PowerShell** and check whether Python is available:

```powershell
python --version
```

If Python is not found, install Python 3.12 for your Windows user:

```powershell
winget install --id Python.Python.3.12 --exact --scope user
```

Accept the package/source prompts. When installation finishes, close and reopen PowerShell. The `py` launcher may not be installed; the steps below use `python` or Python's full path.

## 2. Open the project folder

In PowerShell, run:

```powershell
$root = 'D:\Projects\windows WSL to server'
Set-Location -LiteralPath $root
```

Confirm the prompt starts with `PS D:\Projects\windows WSL to server>`. Run the remaining commands in this same PowerShell window; if you open another window, define `$root` again.

## 3. Create a virtual environment and install dependencies

Try the Python command first:

```powershell
python --version
python -m venv "$root\.venv"
```

If `python` is not recognized but Python 3.12 was installed for your user, use:

```powershell
$basePython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
& $basePython --version
& $basePython -m venv "$root\.venv"
```

Then install requirements into the project-local virtual environment:

```powershell
$python = "$root\.venv\Scripts\python.exe"
& $python -m pip install -r "$root\requirements.txt"
```

Wait for pip to report that Flask, psutil, and the other requirements were installed successfully. `.venv` keeps packages separate from other Python projects.

## 4. Create your dashboard login

Create the local data folders and configuration:

```powershell
New-Item -ItemType Directory -Force -Path "$root\instance", "$root\tasks" | Out-Null
& $python "$root\create_config.py" "$root\instance\config.json"
```

At the prompts:

1. Enter a dashboard username, or press Enter to use `admin`.
2. Enter a strong password of at least **12 characters**, then confirm it. Password input is hidden. Do not share the password.
3. For a local-only preview, enter `127.0.0.1/32` at `Allowed network:`.
4. Press Enter at the next blank `Allowed network:` prompt to finish.

`127.0.0.1/32` allows this PC only. Do not enter your PC's individual LAN address as a network CIDR. The config generator refuses to overwrite an existing config file.

Wait for a message such as:

```text
Configuration saved to D:\Projects\windows WSL to server\instance\config.json
```

If password validation fails, run the config command again with a password of at least 12 characters.

## 5. Start HomeDash

In the same PowerShell window, configure the local preview and start Flask:

```powershell
$env:HOMEDASH_COOKIE_SECURE = '0'
$env:HOMEDASH_CONFIG = "$root\instance\config.json"
$env:HOMEDASH_POLICY = "$root\instance\policy.json"
$env:HOMEDASH_TASKS = "$root\tasks"
& $python -m flask --app 'app:create_app()' run --host 127.0.0.1
```

When Flask reports `http://127.0.0.1:5000`, open **http://127.0.0.1:5000** in your browser and sign in with the username/password you created. Keep the PowerShell window open; press **Ctrl+C** in that window to stop the app.

## 6. Run tests (optional)

In another PowerShell window:

```powershell
$root = 'D:\Projects\windows WSL to server'
$python = "$root\.venv\Scripts\python.exe"
Set-Location -LiteralPath $root
& $python -m unittest discover -s tests -v
```

## Troubleshooting

- **`py` is not recognized:** Use `python`, or the Python 3.12 full path shown in Step 3.
- **Access denied creating `.venv`:** Ensure PowerShell is in the project folder, not `C:\Windows\system32`. Run `Set-Location -LiteralPath $root` first. Do not create the virtual environment under a Windows system folder.
- **Config file not found:** Config creation did not finish. Rerun Step 4, use a password of at least 12 characters, enter `127.0.0.1/32`, then submit the blank network prompt.
- **Local access gets HTTP 403:** The config must contain `127.0.0.1/32`. Preserve any existing config before changing it; never post its secret key or password hash publicly.
- **Port 5000 is already in use:** Stop the other local development server or add `--port 5001` to the Flask command; then browse to `http://127.0.0.1:5001`.
- **Flask cannot import the app:** Confirm the current folder is the project root and pip installation completed successfully.

## Windows preview limitations

On Windows, HomeDash can display host metrics. Linux `systemd` service management, cron tasks, and the privileged apply helper require Ubuntu/Linux and are not provided by this Windows preview. The development server is bound to localhost. Do not bind it to every interface, expose it to the LAN, or publish it to the internet.
