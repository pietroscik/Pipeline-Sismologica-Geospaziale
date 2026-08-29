from cryptography.fernet import Fernet

from mobile.alert_system import AlertSystem
from scripts.encrypt_credentials import encrypt_value


def test_credential_utility_tokens_decrypt_in_alert_system():
    """The CLI utility and runtime must use the same Fernet-key convention."""
    key = Fernet.generate_key().decode()
    encrypted = encrypt_value("secret_value", key)

    alert_system = AlertSystem(config={"encryption_key": key})

    assert alert_system._decrypt_value(encrypted.removeprefix("ENC:")) == "secret_value"
