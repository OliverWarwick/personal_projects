capture_better.py
=================

This helper script opens a browser using Playwright, records a HAR file, and saves the current page DOM (HTML). It's designed to help capture network activity and page content for discovery when building programmatic clients for Better.com.

Usage
-----

1. Install Playwright and browsers (macOS):

```bash
python3 -m pip install --user playwright
python3 -m playwright install
```

2. Run the capture script (headful recommended for manual login):

```bash
python3 personal_project/scripts/capture_better.py https://better.com
```

Options
-------
- `--out-dir`: directory where outputs will be written (default: `personal_project/data/better_com_capture/`).
- `--screenshot`: optional screenshot filename (saves PNG).
- `--wait-selector`: CSS selector to wait for before saving (e.g. `.account-name`).
- `--timeout`: auto-save after N seconds (useful for headless runs).
- `--headless`: run in headless mode.

Security & Privacy
------------------
- HAR files and Playwright `storage_state` may contain session cookies and tokens—treat them as sensitive and do not commit them to version control.
- Use local machine keychain for credential storage (keyring) rather than storing plaintext credentials in the repo.

Next steps
----------
Run the script, perform a manual login and any booking flows you want inspected, then attach the HAR and HTML outputs from `personal_project/data/better_com_capture/` so discovery can proceed.

