#!/usr/bin/env python3
"""
Utility script to encrypt sensitive credentials for secure storage in config files.

This script uses Fernet symmetric encryption (AES-128 in CBC mode) to encrypt
sensitive values like passwords, API keys, and tokens.

Environment Variables:
    ENCRYPTION_KEY: The encryption key (32 url-safe base64-encoded bytes)
                    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Usage:
    # Generate a new encryption key
    python scripts/encrypt_credentials.py --generate-key
    
    # Encrypt a value
    python scripts/encrypt_credentials.py --encrypt --value "my_password"
    
    # Encrypt multiple values from a file
    python scripts/encrypt_credentials.py --encrypt-file input.txt --output encrypted.txt
    
    # Test decryption (verifies the encryption key works)
    python scripts/encrypt_credentials.py --test-decrypt --value "ENC:..."

The encrypted values are prefixed with 'ENC:' and can be stored in YAML config files.
The AlertSystem will automatically decrypt them at runtime if ENCRYPTION_KEY is set.
"""

import argparse
import os
import sys
from pathlib import Path

# Import PROJECT_ROOT for consistent path resolution
from path_utils import PROJECT_ROOT


def generate_key() -> str:
    """Generate a new Fernet encryption key."""
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        return key.decode()
    except ImportError:
        print("Error: cryptography library not installed. Install with: pip install cryptography")
        sys.exit(1)


def encrypt_value(value: str, encryption_key: str) -> str:
    """Encrypt a value using Fernet encryption."""
    try:
        from cryptography.fernet import Fernet
        fernet = Fernet(encryption_key.encode())
        encrypted = fernet.encrypt(value.encode())
        return f"ENC:{encrypted.decode()}"
    except ImportError:
        print("Error: cryptography library not installed. Install with: pip install cryptography")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def decrypt_value(encrypted_value: str, encryption_key: str) -> str:
    """Decrypt a value that was encrypted with encrypt_value."""
    if not encrypted_value.startswith("ENC:"):
        print("Error: Value does not appear to be encrypted (missing 'ENC:' prefix)")
        sys.exit(1)
    
    try:
        from cryptography.fernet import Fernet
        fernet = Fernet(encryption_key.encode())
        decrypted = fernet.decrypt(encrypted_value[4:].encode())
        return decrypted.decode()
    except ImportError:
        print("Error: cryptography library not installed. Install with: pip install cryptography")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def encrypt_file(input_path: Path, output_path: Path, encryption_key: str) -> None:
    """Encrypt all values in a file (one per line)."""
    try:
        with open(input_path, 'r') as f:
            lines = f.readlines()
        
        encrypted_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                encrypted = encrypt_value(line, encryption_key)
                encrypted_lines.append(f"{encrypted}")
            else:
                encrypted_lines.append(f"{line}")
        
        with open(output_path, 'w') as f:
            f.writelines(encrypted_lines)
        
        print(f"Successfully encrypted {len([l for l in lines if l.strip() and not l.strip().startswith('#')])} values")
        print(f"Output written to: {output_path}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def decrypt_file(input_path: Path, output_path: Path, encryption_key: str) -> None:
    """Decrypt all values in a file (one per line)."""
    try:
        with open(input_path, 'r') as f:
            lines = f.readlines()
        
        decrypted_lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and line.startswith("ENC:"):
                decrypted = decrypt_value(line, encryption_key)
                decrypted_lines.append(f"{decrypted}\n")
            else:
                decrypted_lines.append(f"{line}\n")
        
        with open(output_path, 'w') as f:
            f.writelines(decrypted_lines)
        
        print(f"Successfully decrypted {len([l for l in lines if l.strip() and not l.strip().startswith('#') and l.strip().startswith('ENC:')])} values")
        print(f"Output written to: {output_path}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Encrypt/decrypt sensitive credentials for configuration files"
    )
    
    # Key management
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help="Generate a new encryption key"
    )
    
    # Encryption
    parser.add_argument(
        "--encrypt",
        action="store_true",
        help="Encrypt a single value"
    )
    parser.add_argument(
        "--value",
        type=str,
        help="Value to encrypt or decrypt"
    )
    
    # File operations
    parser.add_argument(
        "--encrypt-file",
        type=Path,
        help="Path to file containing values to encrypt (one per line)"
    )
    parser.add_argument(
        "--decrypt-file",
        type=Path,
        help="Path to file containing encrypted values to decrypt (one per line)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path (default: encrypted.txt or decrypted.txt)"
    )
    
    # Test
    parser.add_argument(
        "--test-decrypt",
        action="store_true",
        help="Test decryption of a value"
    )
    
    args = parser.parse_args()
    
    # Get encryption key
    encryption_key = os.getenv('ENCRYPTION_KEY')
    if not encryption_key and not args.generate_key:
        print("Error: ENCRYPTION_KEY environment variable not set")
        print("Generate a key with: python scripts/encrypt_credentials.py --generate-key")
        sys.exit(1)
    
    # Generate key
    if args.generate_key:
        key = generate_key()
        print(f"New encryption key: {key}")
        print("\nSet this as your ENCRYPTION_KEY environment variable:")
        print(f"  export ENCRYPTION_KEY='{key}'")
        print("\nAdd to your .env file:")
        print(f"  ENCRYPTION_KEY={key}")
        return
    
    # Single value encryption
    if args.encrypt and args.value:
        encrypted = encrypt_value(args.value, encryption_key)
        print(f"Encrypted value: {encrypted}")
        return
    
    # Single value decryption test
    if args.test_decrypt and args.value:
        decrypted = decrypt_value(args.value, encryption_key)
        print(f"Decrypted value: {decrypted}")
        return
    
    # File encryption
    if args.encrypt_file:
        output_path = args.output or Path("encrypted.txt")
        encrypt_file(args.encrypt_file, output_path, encryption_key)
        return
    
    # File decryption
    if args.decrypt_file:
        output_path = args.output or Path("decrypted.txt")
        decrypt_file(args.decrypt_file, output_path, encryption_key)
        return
    
    # No action specified
    parser.print_help()


if __name__ == "__main__":
    main()
