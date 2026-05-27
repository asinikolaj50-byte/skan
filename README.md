# OSINT Combo Bot

A unified OSINT tool that combines **holehe** (email OSINT) and **user-scanner** (username scanner) into a single command-line bot.

- **Email mode** — checks if an email is registered on 120+ websites using the password recovery method (based on [holehe](https://github.com/megadose/holehe))
- **Username mode** — searches for a username across 100+ platforms (based on [user-scanner](https://github.com/kaifcodec/user-scanner))
- **Auto-detect** — just pass an email or username and it figures out which mode to use

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/osint-combo-bot.git
cd osint-combo-bot
pip install -r requirements.txt
```

## Usage

```bash
# Auto-detect: email → holehe scan
python main.py target@example.com

# Auto-detect: username → user-scanner
python main.py johndoe

# Run both holehe + username scan for an email
python main.py target@example.com --both

# Only show found results (cleaner output)
python main.py johndoe --only-found

# Scan only a specific category (dev, social, gaming, etc.)
python main.py johndoe -c dev

# Skip NSFW sites
python main.py johndoe --no-nsfw

# Verbose output (show URLs)
python main.py johndoe -v

# Force email mode
python main.py johndoe@gmail.com --mode email

# Force username mode
python main.py someuser --mode username

# Set request timeout (default: 10s)
python main.py target@example.com -t 15
```

## Options

| Flag | Description |
|------|-------------|
| `--mode` | `auto` (default), `email`, or `username` |
| `--both` | Run both email OSINT and username scan |
| `--only-found` | Only show sites where the target was found |
| `-v, --verbose` | Verbose output with URLs |
| `-c, --category` | Scan a specific category (username mode only) |
| `--no-nsfw` | Skip adult/NSFW platforms |
| `-t, --timeout` | Request timeout in seconds (default: 10) |

## Credits

- [holehe](https://github.com/megadose/holehe) by @megadose — email OSINT via password recovery
- [user-scanner](https://github.com/kaifcodec/user-scanner) by @kaifcodec — username OSINT
