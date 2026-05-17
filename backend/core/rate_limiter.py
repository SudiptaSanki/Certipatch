import json
import os

# Consumer Gmail (@gmail.com) is commonly limited to about 500 recipients per rolling
# 24 hours for SMTP/app sending; Google Workspace limits are higher. See:
# https://support.google.com/mail/answer/22839
DEFAULT_FREE_GMAIL_SMTP_DAILY = 500
DEFAULT_ROLLING_WINDOW_HOURS = 24


class AccountManager:
    def __init__(self, config_path):
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
            
        self.accounts = self.config.get("accounts", [])
        if not self.accounts:
            raise ValueError("No accounts found in config file.")
            
        self.email_settings = self.config.get("email_settings", {})
        self.current_index = 0

    def _sending_limits(self):
        raw = self.config.get("sending_limits")
        return raw if isinstance(raw, dict) else {}

    def get_default_daily_limit(self):
        """Max sends per account per rolling window (free Gmail SMTP ≈ 500/day)."""
        sl = self._sending_limits()
        v = sl.get(
            "smtp_daily_per_account_default",
            sl.get("gmail_free_smtp_daily_per_account", DEFAULT_FREE_GMAIL_SMTP_DAILY),
        )
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = DEFAULT_FREE_GMAIL_SMTP_DAILY
        return max(1, min(n, 50_000))

    def get_rolling_window_hours(self):
        sl = self._sending_limits()
        v = sl.get("rolling_window_hours", DEFAULT_ROLLING_WINDOW_HOURS)
        try:
            h = int(v)
        except (TypeError, ValueError):
            h = DEFAULT_ROLLING_WINDOW_HOURS
        return max(1, min(h, 168))

    def iter_accounts_with_limits(self):
        """Yields (email, daily_limit) for quota math and UI."""
        default = self.get_default_daily_limit()
        for acc in self.accounts:
            raw = acc.get("daily_send_limit", default)
            try:
                lim = int(raw)
            except (TypeError, ValueError):
                lim = default
            lim = max(1, min(lim, 50_000))
            yield acc["email"], lim

    def get_next_account(self):
        """Returns the next account in the rotation list."""
        account = self.accounts[self.current_index]
        # Move to the next index, loop back to 0 if at the end of the list
        self.current_index = (self.current_index + 1) % len(self.accounts)
        return account['email'], account['password']

    def get_template(self):
        """Returns the subject and body template."""
        return self.email_settings.get("subject", "Certificate"), self.email_settings.get("body_template", "")