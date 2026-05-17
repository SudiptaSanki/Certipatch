import json
import os

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

    def get_next_account(self):
        """Returns the next account in the rotation list."""
        account = self.accounts[self.current_index]
        # Move to the next index, loop back to 0 if at the end of the list
        self.current_index = (self.current_index + 1) % len(self.accounts)
        return account['email'], account['password']

    def get_template(self):
        """Returns the subject and body template."""
        return self.email_settings.get("subject", "Certificate"), self.email_settings.get("body_template", "")